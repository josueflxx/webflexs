
import re
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

class ClampParser:
    """
    Parser especializado para extraer especificaciones técnicas de Abrazaderas
    a partir de descripciones de texto plano.
    """
    
    @staticmethod
    def normalize_text(text: str) -> str:
        """
        Normaliza el texto: mayúsculas, espacios, reemplazos variantes.
        """
        if not text:
            return ""
        
        # 1. Mayúsculas
        text = text.upper().strip()
        
        # 2. Reemplazos variantes
        replacements = {
            'S/CURVA': 'SEMICURVA',
            'S-CURVA': 'SEMICURVA',
            'CURV.': 'CURVA',
            'S/C': 'SEMICURVA',
        }
        
        for k, v in replacements.items():
            text = text.replace(k, v)
            
        # 3. Espacios duplicados
        text = re.sub(r'\s+', ' ', text)
        
        # 4. Normalizar separadores (ej: " X " -> " X ")
        # Asegurar espacios alrededor de X para facilitar tokenización/regex
        text = re.sub(r'\s*X\s*', ' X ', text)
        
        return text

    @classmethod
    def parse(cls, text: str) -> Dict[str, Any]:
        """
        Analiza el texto y retorna estructura con datos y confianza.
        """
        text = cls.normalize_text(text)
        
        result = {
            'fabrication': None,
            'diameter': None,
            'width': None,
            'length': None,
            'shape': None,
            'parse_confidence': 100,
            'parse_warnings': []
        }
        
        # Check condition: Start with ABRAZADERA (optional check here, usually done by caller)
        # We proceed assuming it IS an abrazadera description
        
        # 1. Fabricación
        has_trefilada = 'TREFILADA' in text
        has_laminada = 'LAMINADA' in text
        
        if has_trefilada and has_laminada:
            result['parse_warnings'].append("Conflicto: TEXTO contiene TREFILADA y LAMINADA")
            result['parse_confidence'] -= 20
        elif has_trefilada:
            result['fabrication'] = 'TREFILADA'
        elif has_laminada:
            result['fabrication'] = 'LAMINADA'
        else:
            result['parse_warnings'].append("Falta: Fabricación no detectada")
            result['parse_confidence'] -= 20

        # 2. Diámetro (Token after DE)
        # Regex to find " DE {TOKEN} "
        match_diam = re.search(r'\bDE\s+([\d/]+|\d+)', text)
        if match_diam:
            result['diameter'] = match_diam.group(1)
        else:
            # Fallback: Maybe looks like "ABRAZADERA 1/2" directly?
            # Let's stick to "DE" rule first as requested.
            result['parse_warnings'].append("Falta: Diámetro (no se encontró 'DE ...')")
            result['parse_confidence'] -= 20

        # 3. Medidas (Ancho X Largo)
        # Pattern: digits X digits
        # We look for all numbers and see what surrounds 'X'
        # Or simpler: find "DE {DIAM} X {ANCHO} X {LARGO}" often happens?
        # User said: "Luego medidas separadas por X"
        # Example: 1/2 X 85 X 260
        # Wait, the structure is usually: DIAM X ANCHO X LARGO? 
        # User example: "DE 1/2 X 85 X 260"
        # It seems the diameter is extracted by "DE", but it participates in the X chain?
        # Let's look at "X" separated integers.
        # "85 X 260"
        
        # Let's find integer pairs separated by X that look like dimensions
        # Exclude the diameter if it was found before.
        
        # Strategy: find all numbers separated by X
        # Regex: (\d+)\s*X\s*(\d+)
        
        # But wait, looking at example: "DE 1/2 X 85 X 260"
        # 1/2 is diameter. Then X, then 85 (width?), then X, then 260 (length?)
        # Or is 85 width and 260 length?
        # "El primer número detectado -> ancho. El segundo número detectado -> largo" (Patrones X número)
        
        # Let's scan for integers after the diameter part?
        # Or simple regex: look for " X (\d+) X (\d+)" pattern?
        # Or look for any sequence of "X number"
        
        dimensions = re.findall(r'\sX\s(\d+)', text)
        if len(dimensions) >= 2:
            result['width'] = int(dimensions[0])
            result['length'] = int(dimensions[1])
        elif len(dimensions) == 1:
            result['width'] = int(dimensions[0])
            result['parse_warnings'].append("Falta: Largo (solo se encontró 1 medida)")
            result['parse_confidence'] -= 10
        else:
            result['parse_warnings'].append("Falta: Ancho y Largo (no se encontraron patrones 'X number')")
            result['parse_confidence'] -= 20
            
        # 4. Forma
        # Priority: SEMICURVA > CURVA > PLANA
        if 'SEMICURVA' in text:
            result['shape'] = 'SEMICURVA'
        elif 'CURVA' in text:
            # Check it's not actually Semicurva (already handled by priority if-elif order)
            result['shape'] = 'CURVA'
        elif 'PLANA' in text:
            result['shape'] = 'PLANA'
        else:
             result['parse_warnings'].append("Falta: Forma")
             result['parse_confidence'] -= 10

        return result
