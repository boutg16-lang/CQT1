import re
from urllib.parse import urljoin, urlsplit


class PolicyError(ValueError):
    pass


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SECRET_LABEL = re.compile(
    r"api[\s_-]*key|access[\s_-]*token|client[\s_-]*secret|"
    r"password|passphrase|secret|token|authorization|cookie|credentials?|"
    r"senha|contrase(?:ñ|n)a|mot\s+de\s+passe|şifre|"
    r"كلمة\s*(?:المرور|السر)|مفتاح.*(?:api|سر)|رمز\s*(?:الوصول|التحقق)|"
    r"(?:api|access).{0,12}(?:key|token|secret)",
    re.IGNORECASE,
)
_ROLES = {"button", "link", "tab", "checkbox", "radio", "textbox", "combobox"}
_KEYS = {"Enter", "Escape", "Tab", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"}


def validate_project_url(value):
    if not isinstance(value, str) or len(value) > 300:
        raise PolicyError("Project URL is invalid")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port if parsed.port is not None else 80
    except ValueError as exc:
        raise PolicyError("Project URL is invalid") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "http" or host not in _LOCAL_HOSTS:
        raise PolicyError("Only an HTTP project running on localhost is allowed")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PolicyError("Project URL must not contain credentials, a query, or a fragment")
    if parsed.path not in {"", "/"}:
        raise PolicyError("Project URL must point to the project root")
    if not 1 <= port <= 65535:
        raise PolicyError("Project port must be between 1 and 65535")
    display_host = "[::1]" if host == "::1" else host
    return "http://{}:{}".format(display_host, port)


def origin_of_url(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port if parsed.port is not None else 80
    except (TypeError, ValueError) as exc:
        raise PolicyError("URL origin is invalid") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "http" or host not in _LOCAL_HOSTS:
        raise PolicyError("Only localhost project URLs are allowed")
    if parsed.username or parsed.password or not 1 <= port <= 65535:
        raise PolicyError("URL origin is invalid")
    display_host = "[::1]" if host == "::1" else host
    return "http://{}:{}".format(display_host, port)


def project_origin(value):
    return origin_of_url(validate_project_url(value))


def validate_navigation_path(value, base_url):
    if not isinstance(value, str) or len(value) > 1200 or not value.startswith("/") or value.startswith("//"):
        raise PolicyError("Navigation must use a path on the local project")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment or "\\" in value:
        raise PolicyError("Navigation must use a path on the local project")
    if any(part in {".", ".."} for part in parsed.path.split("/")):
        raise PolicyError("Relative path traversal is not allowed")
    target = urljoin(validate_project_url(base_url) + "/", value.lstrip("/"))
    if origin_of_url(target) != project_origin(base_url):
        raise PolicyError("Navigation left the local project")
    return target


_HIGH_IMPACT_LABEL = re.compile(
    r"\b(?:process(?:ing|ar)?|generate|render(?:ing)?|publish(?:ing)?|upload(?:ing)?|"
    r"download(?:ing)?|transcrib(?:e|ing)|delete|remove|export|submit|run|save|saving|update|apply|start)\b|"
    r"choose\s+file|browse\s+files|select\s+file|attach\s+file|"
    r"(?:processamento|publicar|enviar|carregar|baixar|renderizar|transcrever|"
    r"excluir|remover|exportar|gerar|salvar|atualizar|iniciar|guardar|subir|procesar|"
    r"معالجة|ابدأ|تشغيل|توليد|إنتاج|نشر|رفع|تحميل|حذف|احفظ|تحديث)",
    re.IGNORECASE,
)
_HIGH_IMPACT_LABEL_EXTRA = re.compile(
    r"\b(?:create|confirm|approve|commit|execute|convert|transcode|translate|"
    r"analy[sz](?:e|ing|ed))\b|"
    r"(?:criar|confirmar|aprovar|executar|converter|traduzir|"
    r"إنشاء|تأكيد|موافقة|تنفيذ|تحويل|ترجمة)",
    re.IGNORECASE,
)
_INLINE_SECRET = re.compile(
    r"(?i)\b((?:api|access|client|refresh|auth)[\s_-]*(?:key|token|secret)|"
    r"password|passphrase|token|secret|authorization|cookie)\s*[:=]\s*([^\s,;]+)"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}={0,2}")
_TOKEN_SHAPE = re.compile(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b")


def is_sensitive_control_name(value):
    return isinstance(value, str) and bool(_SECRET_LABEL.search(value))


def redact_sensitive_text(value):
    if not isinstance(value, str):
        return ""
    text = _INLINE_SECRET.sub(lambda match: match.group(1) + match.group(0)[len(match.group(1)):].split(match.group(2), 1)[0] + "[REDACTED]", value)
    text = _BEARER_SECRET.sub("Bearer [REDACTED]", text)
    return _TOKEN_SHAPE.sub("[REDACTED_TOKEN]", text)


def validate_action(value):
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise PolicyError("Action must be a JSON object with a type")
    action_type = value["type"]
    schemas = {
        "snapshot": {"type"},
        "screenshot": {"type"},
        "navigate": {"type", "path"},
        "click": {"type", "role", "name"},
        "fill": {"type", "name", "value"},
        "select": {"type", "name", "label"},
        "press": {"type", "name", "key"},
        "scroll": {"type", "direction", "pixels"},
        "wait": {"type", "milliseconds"},
    }
    required = {
        "snapshot": {"type"},
        "screenshot": {"type"},
        "navigate": {"type", "path"},
        "click": {"type", "role", "name"},
        "fill": {"type", "name", "value"},
        "select": {"type", "name", "label"},
        "press": {"type", "name", "key"},
        "scroll": {"type", "direction", "pixels"},
        "wait": {"type", "milliseconds"},
    }
    if action_type not in schemas:
        raise PolicyError("Action type is not allowed")
    if set(value) != required[action_type]:
        raise PolicyError("Action fields do not match the allowed schema")
    action = dict(value)
    if action_type in {"click", "fill", "select", "press"}:
        name = action["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise PolicyError("Control name must be a non-empty string of at most 160 characters")
        if is_sensitive_control_name(name):
            raise PolicyError("Secret and credential fields cannot be controlled")
        action["name"] = name.strip()
    if action_type == "click" and (not isinstance(action["role"], str) or action["role"] not in _ROLES - {"textbox", "combobox"}):
        raise PolicyError("Unsupported control role")
    if action_type == "fill":
        if not isinstance(action["value"], str) or len(action["value"]) > 4000:
            raise PolicyError("Text input must be a string of at most 4000 characters")
    if action_type == "select":
        if not isinstance(action["label"], str) or not action["label"].strip() or len(action["label"]) > 160:
            raise PolicyError("Option label is invalid")
        action["label"] = action["label"].strip()
    if action_type == "press" and (not isinstance(action["key"], str) or action["key"] not in _KEYS):
        raise PolicyError("Keyboard key is not allowed")
    if action_type == "scroll":
        if not isinstance(action["direction"], str) or action["direction"] not in {"up", "down"} or type(action["pixels"]) is not int or not 0 <= action["pixels"] <= 1000:
            raise PolicyError("Scroll action is outside its allowed range")
    if action_type == "wait":
        if type(action["milliseconds"]) is not int or not 0 <= action["milliseconds"] <= 8000:
            raise PolicyError("Wait must be between 0 and 8000 milliseconds")
    return action


def requires_manual_approval(value):
    action = validate_action(value)
    if action["type"] == "click" and (
        _HIGH_IMPACT_LABEL.search(action["name"])
        or _HIGH_IMPACT_LABEL_EXTRA.search(action["name"])
    ):
        return True
    return action["type"] == "press" and action["key"] == "Enter"
