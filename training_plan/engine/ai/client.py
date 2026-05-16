from training_plan.core.common import *

# ══════════════════════════════════════════════════════════════════════════════
# AI – provider factory
# ══════════════════════════════════════════════════════════════════════════════

_EXHAUSTED_MODELS = set()
_MODEL_LAST_REQUEST_TS: dict[str, float] = {}
_DEFAULT_MIN_REQUEST_INTERVAL = float(os.getenv("AI_MIN_REQUEST_INTERVAL_SEC", "6.0"))
_OLLAMA_THINK_DEFAULT = os.getenv("OLLAMA_THINK", "").strip().lower() in {"1", "true", "yes", "on"}


def _maybe_wait_for_rate_limit(provider: str, model_name: str):
    if provider != "gemini":
        return
    now = time.time()
    key = f"{provider}:{model_name}"
    min_interval = _DEFAULT_MIN_REQUEST_INTERVAL
    last_ts = _MODEL_LAST_REQUEST_TS.get(key)
    if last_ts is None:
        return
    elapsed = now - last_ts
    wait_time = max(0.0, min_interval - elapsed)
    if wait_time > 0.05:
        log.info(f"   Waiting {wait_time:.2f}s to respect adaptive rate limit for {model_name}...")
        time.sleep(wait_time)


def _mark_rate_limited(provider: str, model_name: str):
    if provider != "gemini":
        return
    _MODEL_LAST_REQUEST_TS[f"{provider}:{model_name}"] = time.time()


def _ollama_generate(url: str, payload: dict) -> dict:
    resp = requests.post(url, json=payload, timeout=600)
    resp.raise_for_status()
    return resp.json()

def call_ai(provider, prompt, temperature: float | None = None):
    global _EXHAUSTED_MODELS
    if provider == "gemini":
        from google import genai
        from google.genai import types

        key = os.getenv("GEMINI_API_KEY", "")
        if not key:
            sys.exit("Set GEMINI_API_KEY.")

        client = genai.Client(api_key=key, http_options={"timeout": 120_000})
        models_str = os.getenv("GEMINI_MODELS")
        model_queue = [m.strip() for m in models_str.split(",") if m.strip()]
        
        active_models = [m for m in model_queue if m not in _EXHAUSTED_MODELS]
        if not active_models:
            log.warning("All Gemini models exhausted. Falling back to Mistral AI.")
            return call_ai("mistral", prompt)

        log.info(f"Sending to Gemini ({len(active_models)} models in queue)...")

        last_err = None
        for current_model in active_models:
            for attempt in range(1, 4):
                try:
                    _maybe_wait_for_rate_limit(provider, current_model)
                    log.info(f"   Trying {current_model} (attempt {attempt})...")
                    response = client.models.generate_content(
                        model=current_model, contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=temperature,
                        ),
                    )
                    os.environ["_USED_MODEL"] = current_model
                    _mark_rate_limited(provider, current_model)
                    return response.text
                except Exception as e:
                    import httpx
                    last_err = e
                    if isinstance(e, httpx.ReadTimeout):
                        log.warning(f"   {current_model} timeout – trying next model")
                        break
                    
                    status = getattr(e, 'status_code', getattr(e, 'code', 0))
                    if status in (429, 503) or '429' in str(e) or '503' in str(e) or 'RESOURCE_EXHAUSTED' in str(e):
                        if attempt < 3:
                            wait_time = 30 * attempt
                            log.warning(f"   {current_model} {status} – waiting {wait_time}s...")
                            time.sleep(wait_time)
                        else:
                            log.warning(f"   {current_model} failed ({status}) – marking as exhausted.")
                            _EXHAUSTED_MODELS.add(current_model)
                            break
                    else:
                        log.warning(f"   {current_model} failed ({status}): {e}")
                        break
        raise last_err
    
    elif provider == "anthropic":
        import anthropic
        key = os.getenv("ANTHROPIC_API_KEY","")
        if not key: sys.exit("Set ANTHROPIC_API_KEY.")
        mn = os.getenv("ANTHROPIC_MODEL","claude-opus-4-5")
        log.info(f"Sending to Anthropic ({mn})...")
        return anthropic.Anthropic(api_key=key).messages.create(
            model=mn,
            max_tokens=6000,
            temperature=temperature if temperature is not None else 0,
            messages=[{"role":"user","content":prompt}],
        ).content[0].text
    elif provider == "openai":
        from openai import OpenAI
        base_url = os.getenv("OPENAI_BASE_URL")  # e.g. http://localhost:8081/v1
        key = os.getenv("OPENAI_API_KEY", "")

        # Real OpenAI requires a key; local llama-server doesn't care.
        if not key:
            if base_url:
                key = "sk-no-key"  # llama-server ignores this
            else:
                sys.exit("Set OPENAI_API_KEY (or OPENAI_BASE_URL for a local server).")

        mn = os.getenv("OPENAI_MODEL", "gpt-4o")
        is_local = bool(base_url)

        log.info(f"Sending to {'local OpenAI-compatible server' if is_local else 'OpenAI'} ({mn})...")

        kwargs = {
            "model": mn,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        # Disable Qwen 3.6's thinking mode when talking to llama-server.
        # Harmless on real OpenAI (extra_body is just dropped if unrecognized,
        # but to be safe we only send it locally).
        if is_local:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False}
            }

        client = OpenAI(api_key=key, base_url=base_url) if base_url else OpenAI(api_key=key)
        return client.chat.completions.create(**kwargs).choices[0].message.content

    elif provider == "mistral":
        key = os.getenv("MISTRAL_API_KEY","")
        if not key: sys.exit("Set MISTRAL_API_KEY.")
        mn = os.getenv("MISTRAL_MODEL","mistral-large-latest")
        log.info(f"Sending to Mistral ({mn})...")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": mn,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        if temperature is not None:
            payload["temperature"] = temperature
        resp = requests.post("https://api.mistral.ai/v1/chat/completions", headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        os.environ["_USED_MODEL"] = mn
        return resp.json()["choices"][0]["message"]["content"]
    elif provider == "ollama":
        mn = os.getenv("OLLAMA_MODEL")
        url = "http://localhost:11434/api/generate"

        log.info(f"Sending to Ollama ({mn}) at {url}...")

        payload = {
            "model": mn,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature if temperature is not None else 0.1,
                "num_predict": 4096,
            },
            "think": _OLLAMA_THINK_DEFAULT,
        }
        data = _ollama_generate(url, payload)

        os.environ["_USED_MODEL"] = mn

        # Token-statistik
        if "eval_count" in data and "eval_duration" in data:
            toks = data["eval_count"] / (data["eval_duration"] / 1e9)
            log.info(f"Tokens: {data['eval_count']} | Hastighet: {toks:.1f} tok/s")

        response = data.get("response", "")
        thinking = data.get("thinking", "")
        if not response and thinking:
            log.warning(f"⚠️ Ollama response empty but thinking non-empty ({len(thinking)} chars) - model used thinking-only mode")
            log.debug(f"Thinking preview: {thinking[:200]}")
            if payload.get("think"):
                log.warning("⚠️ Retrying Ollama once with think=False to get a final answer")
                retry_payload = dict(payload)
                retry_payload["think"] = False
                data = _ollama_generate(url, retry_payload)
                if "eval_count" in data and "eval_duration" in data:
                    toks = data["eval_count"] / (data["eval_duration"] / 1e9)
                    log.info(f"Retry tokens: {data['eval_count']} | Hastighet: {toks:.1f} tok/s")
                response = data.get("response", "")
                thinking = data.get("thinking", "")
        elif not response:
            log.warning("⚠️ Ollama response is empty")
            log.debug(f"Raw Ollama keys: {list(data.keys())}")

        return response
    elif provider == "groq":
        from groq import Groq

        key = os.getenv("GROQ_API_KEY", "")
        if not key:
            sys.exit("Set GROQ_API_KEY.")

        client = Groq(api_key=key)

        models_str = os.getenv("GROQ_MODELS")
        model_queue = [m.strip() for m in models_str.split(",") if m.strip()]

        active_models = [m for m in model_queue if m not in _EXHAUSTED_MODELS]
        if not active_models:
            log.warning("All Groq models exhausted. Falling back to Mistral.")
            return call_ai("mistral", prompt)

        log.info(f"Sending to Groq ({len(active_models)} models in queue)...")

        last_err = None

        for current_model in active_models:
            for attempt in range(1, 4):
                try:
                    _maybe_wait_for_rate_limit(provider, current_model)
                    log.info(f"   Trying {current_model} (attempt {attempt})...")

                    kwargs = {
                        "model": current_model,
                        "messages": [{"role": "user", "content": prompt}],
                    }

                    if temperature is not None:
                        kwargs["temperature"] = temperature

                    response = client.chat.completions.create(**kwargs)
                    content = response.choices[0].message.content

                    os.environ["_USED_MODEL"] = current_model
                    _mark_rate_limited(provider, current_model)

                    return content

                except Exception as e:
                    last_err = e
                    status = getattr(e, "status_code", getattr(e, "code", 0))

                    # Groq rate limit / overload handling
                    if status in (429, 503) or "429" in str(e) or "503" in str(e):
                        if attempt < 3:
                            wait_time = 20 * attempt
                            log.warning(f"   {current_model} {status} – waiting {wait_time}s...")
                            time.sleep(wait_time)
                        else:
                            log.warning(f"   {current_model} failed ({status}) – marking as exhausted.")
                            _EXHAUSTED_MODELS.add(current_model)
                            break
                    else:
                        log.warning(f"   {current_model} failed ({status}): {e}")
                        break

        raise last_err
    
