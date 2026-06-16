import time

from groq import Groq

from core.config import settings

client = Groq(api_key=settings.groq_api_key)


def call_llm(prompt: str, system: str = "You are a helpful assistant.", max_retries: int = 3) -> str:
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=settings.groq_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2048,
            )
            return response.choices[0].message.content
        except Exception as e:
            if attempt < max_retries - 1 and ("503" in str(e) or "unavailable" in str(e).lower() or "rate" in str(e).lower()):
                print(f"Groq unavailable, retrying in 2s (attempt {attempt+1}/{max_retries})")
                time.sleep(2)
            else:
                raise
    raise Exception("Groq unavailable after retries")
