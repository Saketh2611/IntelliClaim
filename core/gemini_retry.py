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
                wait = 2 ** attempt
                print(f"Gemini 503/unavailable, retrying in {wait}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
