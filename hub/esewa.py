"""Server-side eSewa ePay V2 integration.

The browser only receives the signed payment form. The secret key is used
here on the server and is never included in the rendered HTML.
"""

import base64
import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


SIGNED_REQUEST_FIELDS = "total_amount,transaction_uuid,product_code"


class EsewaConfigurationError(RuntimeError):
    pass


class EsewaVerificationError(ValueError):
    pass


class EsewaStatusCheckError(EsewaVerificationError):
    pass


def is_configured():
    return bool(
        getattr(settings, "ESEWA_ENABLED", False)
        and getattr(settings, "ESEWA_PRODUCT_CODE", "")
        and getattr(settings, "ESEWA_SECRET_KEY", "")
        and getattr(settings, "ESEWA_FORM_URL", "")
        and getattr(settings, "ESEWA_STATUS_URL", "")
    )


def _require_config():
    if not is_configured():
        raise EsewaConfigurationError(
            "Set the eSewa enabled flag, product code, secret, form URL, and status URL."
        )


def _format_amount(amount):
    try:
        value = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EsewaVerificationError("Invalid eSewa amount.") from exc
    if value <= 0:
        raise EsewaVerificationError("eSewa amount must be greater than zero.")
    return f"{value:.2f}"


def generate_signature(message):
    _require_config()
    digest = hmac.new(
        settings.ESEWA_SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def build_payment_data(amount, transaction_uuid, success_url, failure_url):
    """Build the exact form fields required by ePay V2."""
    _require_config()
    if not transaction_uuid or not all(
        character.isalnum() or character == "-" for character in transaction_uuid
    ):
        raise EsewaVerificationError(
            "eSewa transaction UUID may contain only letters, numbers, and hyphens."
        )

    formatted_amount = _format_amount(amount)
    signature_message = (
        f"total_amount={formatted_amount},"
        f"transaction_uuid={transaction_uuid},"
        f"product_code={settings.ESEWA_PRODUCT_CODE}"
    )
    return {
        "amount": formatted_amount,
        "tax_amount": "0.00",
        "total_amount": formatted_amount,
        "transaction_uuid": transaction_uuid,
        "product_code": settings.ESEWA_PRODUCT_CODE,
        "product_service_charge": "0.00",
        "product_delivery_charge": "0.00",
        "success_url": success_url,
        "failure_url": failure_url,
        "signed_field_names": SIGNED_REQUEST_FIELDS,
        "signature": generate_signature(signature_message),
    }


def decode_response(encoded_data):
    """Decode eSewa's Base64-encoded callback payload."""
    if not encoded_data:
        raise EsewaVerificationError("eSewa response data is missing.")
    try:
        padding = "=" * (-len(encoded_data) % 4)
        decoded = base64.b64decode(encoded_data + padding).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EsewaVerificationError("Invalid eSewa response payload.") from exc
    if not isinstance(payload, dict):
        raise EsewaVerificationError("Invalid eSewa response payload.")
    return payload


def verify_response_signature(payload):
    """Verify the signed fields returned by eSewa before fulfillment."""
    signed_names = payload.get("signed_field_names", "")
    received = payload.get("signature", "")
    if not signed_names or not received:
        raise EsewaVerificationError("eSewa response signature is missing.")

    names = [name.strip() for name in signed_names.split(",") if name.strip()]
    required_names = {"total_amount", "transaction_uuid", "product_code"}
    if not required_names.issubset(names):
        raise EsewaVerificationError("eSewa response signature fields are incomplete.")

    message = ",".join(f"{name}={payload.get(name, '')}" for name in names)
    expected = generate_signature(message)
    if not hmac.compare_digest(str(received), expected):
        raise EsewaVerificationError("eSewa response signature is invalid.")


def status_check(amount, transaction_uuid):
    """Ask eSewa whether the transaction is COMPLETE/PENDING/etc."""
    _require_config()
    query = urlencode(
        {
            "product_code": settings.ESEWA_PRODUCT_CODE,
            "total_amount": _format_amount(amount),
            "transaction_uuid": transaction_uuid,
        }
    )
    status_url = settings.ESEWA_STATUS_URL.rstrip("?")
    request = Request(
        f"{status_url}?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, URLError, OSError) as exc:
        raise EsewaStatusCheckError("Could not reach the eSewa status endpoint.") from exc
    except json.JSONDecodeError as exc:
        raise EsewaStatusCheckError("eSewa returned an invalid status response.") from exc
    if not isinstance(result, dict):
        raise EsewaVerificationError("Invalid eSewa status response.")
    if result.get("code") == 0 and result.get("error_message"):
        raise EsewaStatusCheckError(str(result.get("error_message")))
    return result
