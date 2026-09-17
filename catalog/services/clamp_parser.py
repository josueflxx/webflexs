"""Conservative extraction of clamp dimensions from commercial names."""
import re
import unicodedata
from fractions import Fraction


SPEC_FIELDS = ("fabrication", "diameter", "width", "length", "shape")
DIAMETER_PATTERN = r"(?:\d+[ -]+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?)"


def normalize_diameter(value):
    value = str(value).strip().replace(",", ".")
    mixed = re.fullmatch(r"(\d+)[ -]+(\d+)/(\d+)", value)
    try:
        number = (Fraction(mixed[1]) + Fraction(int(mixed[2]), int(mixed[3]))) if mixed else Fraction(value)
    except (ValueError, ZeroDivisionError):
        raise ValueError("Diámetro inválido") from None
    if number <= 0 or number > 100:
        raise ValueError("Diámetro fuera de rango")
    if mixed or "/" in value:
        whole, rest = divmod(number.numerator, number.denominator)
        return f"{whole} {rest}/{number.denominator}" if whole and rest else str(number)
    return format(float(number), ".8g")


class ClampParser:
    @staticmethod
    def normalize_text(text):
        text = unicodedata.normalize("NFKD", str(text or "").upper())
        text = "".join(c for c in text if not unicodedata.combining(c))
        text = text.replace("×", "X").replace("*", "X")
        text = re.sub(r"\bS\s*[/\-]\s*C(?:URV[AO])?\b|\bSC\b", "SEMICURVA", text)
        text = re.sub(r"\bSEMI[ -]?CURV[AO]\b", "SEMICURVA", text)
        text = re.sub(r"\bCURVO\b|\bCURV\.", "CURVA", text)
        text = re.sub(r"\bPLANO\b", "PLANA", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def parse(cls, text):
        text = cls.normalize_text(text)
        result = dict.fromkeys(SPEC_FIELDS)
        warnings = []
        if not re.match(r"^ABRAZADERAS?\b", text):
            return {**result, "parse_confidence": 0, "parse_warnings": ["Ignorado: no es una abrazadera"]}

        types = re.findall(r"\b(TREFILADA|LAMINADA|FORJADA)\b", text)
        if len(set(types)) == 1:
            result["fabrication"] = types[0]
        elif types:
            warnings.append("Ambigüedad: varios tipos de fabricación")

        dimension_re = rf'(?<![\d/.,-])({DIAMETER_PATTERN})\s*(?:["″]|MM)?\s*X\s*(\d+)\s*(?:MM)?\s*X\s*(\d+)(?![\d.,])'
        matches = list(re.finditer(dimension_re, text))
        if len(matches) == 1:
            match = matches[0]
            try:
                result["diameter"] = normalize_diameter(match[1])
            except ValueError as exc:
                warnings.append(str(exc))
            for field, token in (("width", match[2]), ("length", match[3])):
                if 0 < int(token) <= 10000:
                    result[field] = int(token)
                else:
                    warnings.append(f"Medida inválida: {field}")
        elif matches:
            warnings.append("Ambigüedad: más de un conjunto de medidas")
        else:
            diameter = re.search(rf"\bDE\s+({DIAMETER_PATTERN})(?![\d/.,])", text)
            if diameter:
                try:
                    result["diameter"] = normalize_diameter(diameter[1])
                except ValueError as exc:
                    warnings.append(str(exc))

        shapes = set(re.findall(r"\b(SEMICURVA|CURVA|PLANA)\b", text))
        if len(matches) == 1:
            suffix = re.match(r"\s*(?:MM\s*)?([CPS])\b", text[matches[0].end():])
            if suffix:
                shapes.add({"C": "CURVA", "P": "PLANA", "S": "SEMICURVA"}[suffix[1]])
        if len(shapes) == 1:
            result["shape"] = shapes.pop()
        elif shapes:
            warnings.append("Ambigüedad: varias formas")

        labels = ("Fabricación", "Diámetro", "Ancho", "Largo", "Forma")
        warnings.extend(f"Falta: {label}" for field, label in zip(SPEC_FIELDS, labels) if result[field] is None)
        return {**result, "parse_confidence": max(0, 100 - 15 * len(warnings)), "parse_warnings": warnings}
