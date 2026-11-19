"""Parse an LLM's messy model response into a segment list.

Extracted verbatim from ``scripts/create_viral_segments.py`` (monolith
split, step 1). Pure/deterministic string -> dict helpers: no I/O, no UI,
no global state. The original module re-exports these names so existing
import paths keep working unchanged.
"""

import ast
import json
import logging
import re

logger = logging.getLogger(__name__)


def clean_json_response(response_text):
    """
    Limpa a resposta focando em encontrar o objeto JSON que contém a chave "segments".

    Two passes:
    1. Parse the response AS-IS. Valid JSON containing escaped characters
       (e.g. "line\\nbreak" inside an Arabic title) MUST NOT be normalized —
       rewriting \\n to a raw newline corrupts strict JSON and used to drop
       the whole segment list silently.
    2. Only when nothing parsed: apply the legacy over-escape fix (\\\\n →
       newline, \\\" → quote) for double-escaped/manual-paste payloads and
       retry.
    """
    if not isinstance(response_text, str):
        response_text = str(response_text)

    if not response_text:
        return {"segments": []}

    result = _extract_segments_json(response_text)
    if result["segments"]:
        return result

    try:
        if "\\n" in response_text or '\\"' in response_text:
            normalized = (
                response_text.replace("\\n", "\n")
                .replace('\\"', '"')
                .replace("\\'", "'")
            )
            if normalized != response_text:
                fallback = _extract_segments_json(normalized)
                if fallback["segments"]:
                    return fallback
    except Exception as exc:
        logger.warning(
            "Fallback decoding of the model response failed: %s", type(exc).__name__
        )
    if not result["segments"]:
        logger.warning(
            "Unable to parse a segment list from the model response (%d characters).",
            len(response_text),
        )
    return result


def _extract_segments_json(response_text):
    """
    Estratégia:
    1. Busca a palavra "segments", encontra o '{' anterior e usa raw_decode.
    2. Fallback: Parsear lista de segmentos item a item (recuperação de JSON truncado).
    """
    # 1. Limpeza preliminar
    # Remove tags de pensamento (DeepSeek R1)
    response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL)
    # Models often wrap valid JSON in ```json fences or add a language tag
    # with different casing. Removing the wrapper before raw_decode makes the
    # parser deterministic while the existing fragment fallback handles
    # genuinely truncated replies.
    response_text = re.sub(r"```(?:json)?", "", response_text, flags=re.IGNORECASE)
    response_text = response_text.replace("```", "")

    # 2. Busca pela palavra-chave "segments"
    # Procura índices de todas as ocorrências de 'segments'
    matches = [m.start() for m in re.finditer(r"segments", response_text)]

    if not matches:
        # Se não achou segments, retorna vazio
        return {"segments": []}

    # Tenta extrair JSON válido a partir de cada ocorrência
    for match_idx in matches:
        # Procura o '{' mais próximo ANTES de "segments"
        # Limita busca a 5000 chars para trás para performance
        start_search = max(0, match_idx - 5000)
        snippet_before = response_text[start_search:match_idx]

        # Encontra o ÚLTIMO '{' no snippet
        last_open_rel = snippet_before.rfind("{")

        if last_open_rel != -1:
            real_start = start_search + last_open_rel
            candidate_text = response_text[real_start:]

            # Tentativa A: json.raw_decode
            try:
                decoder = json.JSONDecoder()
                obj, _ = decoder.raw_decode(candidate_text)
                if "segments" in obj and isinstance(obj["segments"], list):
                    return obj
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.debug(
                    "Model response was not a complete JSON object: %s",
                    type(exc).__name__,
                )

            # Tentativa B: ast.literal_eval
            try:
                balance = 0
                in_string = False
                escape = False
                found_end = -1

                for i, char in enumerate(candidate_text):
                    if escape:
                        escape = False
                        continue
                    if char == "\\":
                        escape = True
                        continue
                    if char == "'" or char == '"':
                        in_string = not in_string
                        continue

                    if not in_string:
                        if char == "{":
                            balance += 1
                        elif char == "}":
                            balance -= 1
                            if balance == 0:
                                found_end = i
                                break

                if found_end != -1:
                    clean_cand = candidate_text[: found_end + 1]
                    obj = ast.literal_eval(clean_cand)
                    if "segments" in obj and isinstance(obj["segments"], list):
                        return obj
            except (ValueError, TypeError, SyntaxError) as exc:
                logger.debug(
                    "Model response was not a Python-literal object: %s",
                    type(exc).__name__,
                )

    # 2b. Recovery: dict-shaped "segments" value (numeric-key object).
    # Some models return {"segments": {"0": {...}, "1": {...}}} — or nest
    # segments deeper — instead of a list. Every attempt above requires
    # isinstance(obj["segments"], list) and the fragment parser below
    # requires '"segments": [' so such a response used to yield zero
    # segments. Decode the object value and, when every value is itself a
    # dict, return its values as the segment list (numeric-looking keys in
    # ascending numeric order, otherwise insertion order). Never crashes:
    # any failure falls through to the existing fallbacks untouched.
    try:
        decoder = json.JSONDecoder()
        for dict_match in re.finditer(r'"segments"\s*:\s*\{', response_text):
            try:
                obj, _ = decoder.raw_decode(response_text[dict_match.end() - 1 :])
            except json.JSONDecodeError:
                continue
            if (
                isinstance(obj, dict)
                and obj
                and all(isinstance(value, dict) for value in obj.values())
            ):
                keys = list(obj.keys())
                if all(re.fullmatch(r"[0-9]+", str(key)) for key in keys):
                    keys.sort(key=int)
                return {"segments": [obj[key] for key in keys]}
    except (TypeError, ValueError, RecursionError) as exc:
        logger.debug(
            "Could not recover a model segments object: %s", type(exc).__name__
        )

    # 3. Fallback: Extração bruta de markdown
    try:
        match = re.search(r"```json(.*?)```", response_text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        logger.debug("Model response markdown JSON was invalid: %s", type(exc).__name__)

    # 4. LAST RESORT: Fragment Parser (Para JSON truncado/incompleto)
    # Procura por "segments": [ e tenta parsear item por item
    try:
        match_list = re.search(r'"segments"\s*:\s*\[', response_text)
        if match_list:
            start_pos = match_list.end()
            current_pos = start_pos
            found_segments = []
            decoder = json.JSONDecoder()

            while True:
                while (
                    current_pos < len(response_text)
                    and response_text[current_pos] in " \t\n\r,"
                ):
                    current_pos += 1

                if current_pos >= len(response_text):
                    break

                if response_text[current_pos] == "]":
                    break

                try:
                    obj, end_pos = decoder.raw_decode(response_text[current_pos:])
                    if isinstance(obj, dict):
                        found_segments.append(obj)
                    current_pos += end_pos
                except json.JSONDecodeError:
                    break

            if found_segments:
                print(
                    f"[INFO] Recuperado {len(found_segments)} segmentos de JSON truncado."
                )
                return {"segments": found_segments}
    except (TypeError, ValueError, re.error) as exc:
        logger.warning(
            "Fragment recovery for the model response failed: %s", type(exc).__name__
        )

    return {"segments": []}
