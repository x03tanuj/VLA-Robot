import os
import re
import json
import time
import base64
import logging
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError


# =============================================================================
# CONFIGURATION
# =============================================================================

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemma-3-27b-it:free")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not found in environment variables")

client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL,
)

VALID_ACTIONS = {"pick", "move", "place"}
REQUEST_TIMEOUT_SECONDS = 40
MAX_RETRIES_429 = 3


# =============================================================================
# PROMPT
# =============================================================================

ROBOTICS_PROMPT = """
You are a robotics vision-language-action system.

Task:
Analyze the image and identify the object referenced in the user command.

Rules:
- Return ONLY valid JSON (no markdown, no extra text).
- Identify only objects that are actually visible in the image.
- Do not hallucinate or guess hidden objects.
- Coordinates must represent the CENTER of the target object.
- Coordinates are percentages:
  - x: left=0, right=100
  - y: top=0, bottom=100
- Action must be exactly one of: "pick", "move", "place".
- Confidence must be between 0 and 1.

If object is not found, return:
{
  "object": "unknown",
  "action": "move",
  "x": 50,
  "y": 50,
  "confidence": 0.0
}

Return format (strict JSON object):
{
  "object": "object_name",
  "action": "pick|move|place",
  "x": 0-100,
  "y": 0-100,
  "confidence": 0-1
}

User Command:
"__COMMAND__"
"""


# =============================================================================
# HELPERS
# =============================================================================

def detect_mime_type(image_bytes: bytes) -> str:
    """Detect common image MIME types from magic bytes."""
    if image_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG"):
        return "image/png"
    if image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]:
        return "image/webp"
    return "image/jpeg"


def decode_base64_image(image_b64: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Decode raw base64 or data URI image safely."""
    if not isinstance(image_b64, str) or not image_b64.strip():
        return None, "Missing or invalid 'image'"

    payload = image_b64.strip()

    # Accept both raw base64 and full data URI.
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]

    try:
        image_bytes = base64.b64decode(payload, validate=True)
        if not image_bytes:
            return None, "Image payload is empty"
        return image_bytes, None
    except Exception as exc:
        logger.warning("Base64 decode failed: %s", exc)
        return None, "Invalid base64 image"


def clean_json_response(text: str) -> str:
    """Remove markdown wrappers and extract JSON object body."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    # If there is extra text around JSON, extract first JSON object.
    if not (cleaned.startswith("{") and cleaned.endswith("}")):
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            cleaned = match.group(0)

    return cleaned.strip()


def parse_model_json(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse model response robustly and never raise."""
    if not text:
        return None, "Empty response from model"

    # Step 1: Clean markdown wrappers
    cleaned = clean_json_response(text)

    logger.info("After markdown cleanup: %d chars", len(cleaned))
    print(f"\n=== AFTER MARKDOWN CLEANUP ===\n{cleaned}\n==============================\n")

    # Step 2: Strip whitespace
    cleaned = cleaned.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    cleaned = cleaned.strip()

    logger.info("After strip/code fence removal: %d chars", len(cleaned))
    print(f"\n=== FINAL CLEAN CONTENT ===\n{cleaned}\n===========================\n")

    # Step 3: Safe JSON parsing with detailed logging
    try:
        data = json.loads(cleaned)
        logger.info("JSON parsing successful")
    except json.JSONDecodeError as e:
        logger.error("JSON decode error at line %d, col %d: %s", e.lineno, e.colno, e.msg)
        print("\n=== JSON PARSE ERROR ===")
        print(f"Error: {e.msg} at line {e.lineno}, col {e.colno}")
        print(f"RAW CONTENT:\n{cleaned}")
        print("=======================\n")
        return None, f"Model returned invalid JSON: {e.msg}"

    if not isinstance(data, dict):
        logger.error("Response is not a dict: type=%s", type(data).__name__)
        print(f"\n=== TYPE ERROR ===\nExpected dict, got {type(data).__name__}\n=======================\n")
        return None, "Model response must be a JSON object"

    return data, None


def validate_result(data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate API response contract and value ranges."""
    required_fields = {"object", "action", "x", "y", "confidence"}
    missing = required_fields - set(data.keys())
    if missing:
        return False, f"Missing fields: {sorted(missing)}"

    if data.get("action") not in VALID_ACTIONS:
        return False, "Invalid action; must be one of pick/move/place"

    if not isinstance(data.get("object"), str) or not data.get("object").strip():
        return False, "Invalid object; must be a non-empty string"

    try:
        x = float(data["x"])
        y = float(data["y"])
        confidence = float(data["confidence"])
    except (TypeError, ValueError):
        return False, "x, y, and confidence must be numeric"

    if not (0 <= x <= 100):
        return False, "x must be between 0 and 100"
    if not (0 <= y <= 100):
        return False, "y must be between 0 and 100"
    if not (0 <= confidence <= 1):
        return False, "confidence must be between 0 and 1"

    # Normalize numeric output to float for consistent API contract.
    data["x"] = x
    data["y"] = y
    data["confidence"] = confidence
    data["object"] = data["object"].strip()
    return True, None


def call_openrouter_with_retries(
    prompt: str,
    image_data_uri: str,
    retries_429: int = MAX_RETRIES_429,
) -> str:
    """Call OpenRouter with retry-on-429 behavior."""
    attempt = 0
    while True:
        attempt += 1
        try:
            response = client.chat.completions.create(
                model=OPENROUTER_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                max_tokens=300,
                timeout=REQUEST_TIMEOUT_SECONDS,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": image_data_uri},
                            },
                        ],
                    }
                ],
            )

            # DEBUG: Print raw response structure
            logger.info("OpenRouter response received (HTTP 200 OK)")
            print("\n=== RAW RESPONSE OBJECT ===")
            print(response)
            print("\n=== RESPONSE AS JSON ===")
            print(response.model_dump_json(indent=2))
            print("===========================\n")

            # Extract content via OpenAI SDK format
            content = response.choices[0].message.content if response.choices else None
            logger.info("Extracted content length: %d chars", len(content or ""))
            print(f"\n=== EXTRACTED CONTENT ===\n{content}\n===========================\n")
            return content or ""

        except RateLimitError:
            if attempt > retries_429:
                raise
            backoff = 1.5 * attempt
            logger.warning("OpenRouter 429 received, retrying in %.1fs", backoff)
            time.sleep(backoff)

        except APIStatusError as exc:
            logger.error(
                "OpenRouter API status error model=%s status=%s body=%s",
                OPENROUTER_MODEL,
                getattr(exc, "status_code", None),
                getattr(getattr(exc, "response", None), "text", None),
            )
            # Some providers surface 429 via generic status error.
            if exc.status_code == 429 and attempt <= retries_429:
                backoff = 1.5 * attempt
                logger.warning("OpenRouter status 429 received, retrying in %.1fs", backoff)
                time.sleep(backoff)
                continue
            raise


# =============================================================================
# ROUTES
# =============================================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/analyze", methods=["POST"])
def analyze():
    """Analyze image + command using OpenRouter vision model."""
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be valid JSON"}), 400

        image_b64 = body.get("image")
        command = body.get("command")

        if not isinstance(command, str) or not command.strip():
            return jsonify({"error": "Missing or invalid 'command'"}), 400

        image_bytes, image_error = decode_base64_image(image_b64)
        if image_error:
            return jsonify({"error": image_error}), 400

        mime_type = detect_mime_type(image_bytes)
        image_data_uri = "data:%s;base64,%s" % (
            mime_type,
            base64.b64encode(image_bytes).decode("ascii"),
        )

        prompt = ROBOTICS_PROMPT.replace("__COMMAND__", command.strip())

        logger.info(
            "Calling OpenRouter model=%s mime=%s command=%s",
            OPENROUTER_MODEL,
            mime_type,
            command.strip(),
        )

        try:
            raw_content = call_openrouter_with_retries(prompt, image_data_uri)
        except APITimeoutError:
            logger.error("OpenRouter request timed out for model=%s", OPENROUTER_MODEL)
            return jsonify({"error": "Upstream timeout from OpenRouter"}), 504
        except APIConnectionError:
            logger.error("OpenRouter connection error for model=%s", OPENROUTER_MODEL)
            return jsonify({"error": "Could not connect to OpenRouter"}), 502
        except RateLimitError:
            logger.error("OpenRouter rate limit exceeded for model=%s", OPENROUTER_MODEL)
            return jsonify({"error": "OpenRouter rate limited request"}), 429
        except APIStatusError as exc:
            logger.error(
                "OpenRouter API error model=%s status=%s body=%s",
                OPENROUTER_MODEL,
                getattr(exc, "status_code", None),
                getattr(getattr(exc, "response", None), "text", None),
            )
            return jsonify({
                "error": "OpenRouter returned an API error",
                "details": f"status={exc.status_code}",
            }), 502

        result, parse_error = parse_model_json(raw_content)
        if parse_error:
            logger.error("JSON parsing failed: %s", parse_error)
            logger.error("Raw content for debug: %s", raw_content[:500])  # Log first 500 chars
            print(f"\n=== PARSE ERROR DETAILS ===")
            print(f"Error: {parse_error}")
            print(f"Raw response: {raw_content}")
            print("============================\n")
            return jsonify({
                "error": parse_error,
                "raw_response": clean_json_response(raw_content or ""),
            }), 500

        valid, validation_error = validate_result(result)
        if not valid:
            return jsonify({
                "error": validation_error,
                "response": result,
            }), 500

        logger.info(
            "Success object=%s action=%s x=%.2f y=%.2f confidence=%.3f",
            result["object"],
            result["action"],
            result["x"],
            result["y"],
            result["confidence"],
        )

        return jsonify(result), 200

    except Exception as exc:
        logger.exception("Unhandled server error")
        return jsonify({
            "error": "Internal server error",
            "details": str(exc),
        }), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)