import asyncio
import time


async def call_gemini_with_retry(client, model, contents, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=contents,
            )
            return response
        except Exception as e:
            if attempt < max_retries - 1 and ("503" in str(e) or "UNAVAILABLE" in str(e) or "overloaded" in str(e).lower()):
                print(f"Gemini 503/unavailable, retrying in 2s (attempt {attempt+1}/{max_retries})")
                time.sleep(2)
            else:
                raise
    raise Exception("Gemini unavailable after retries")
