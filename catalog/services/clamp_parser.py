
import re
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

class ClampParser:
    """
    Parser especializado para extraer especificaciones técnicas de Abrazaderas
    a partir de descripciones de texto plano.
    
    Implementa la lógica estricta de 8 pasos definida por el usuario.
    """
    
    @staticmethod
    def normalize_text(text: str) -> str:
        """
        PASO 1 – Normalizar el texto
        - Convertir a MAYÚSCULAS
        - Quitar espacios duplicados
        - Unificar variantes conocidas (S/CURVA -> SEMICURVA, etc)
        - Asegurar separadores claros (X rodeado de espacios)
        """
        if not text:
            return ""
        
        # 1. Mayúsculas y stripping
        text = text.upper().strip()
        
        # 2. Reemplazos variantes
        replacements = {
            'S/CURVA': 'SEMICURVA',
            'S-CURVA': 'SEMICURVA',
            'S/C': 'SEMICURVA',
            'CURV.': 'CURVA',
            'SC': 'SEMICURVA',  # Shortcut for Forjadas
        }
        
        for k, v in replacements.items():
            # Use regex for whole word replacement to avoid partial matches if needed,
            # but for SC/S/C usually direct replace is ok if normalized
            # Let's be safer with word boundaries for SC
            if k == 'SC':
                text = re.sub(r'\bSC\b', v, text)
            else:
                text = text.replace(k, v)
            
        # 3. Espacios duplicados
        text = re.sub(r'\s+', ' ', text)
        
        # 4. Asegurar separadores claros para X (dimensiones)
        # "todo X rodeado de espacios -> X"
        text = re.sub(r'\s*X\s*', ' X ', text)
        
        return text.strip()

    @classmethod
    def parse(cls, text: str) -> Dict[str, Any]:
        """
        Analiza el texto y retorna estructura con datos y confianza.
        """
        # PASO 1 - Normalización
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
        
        # PASO 2 – Validar que sea una abrazadera
        if not text.startswith('ABRAZADERA'):
            result['parse_warnings'].append("Ignorado: No comienza con 'ABRAZADERA'")
            result['parse_confidence'] = 0
            return result
        
        # PASO 3 – Detectar tipo de fabricación
        has_trefilada = 'TREFILADA' in text
        has_laminada = 'LAMINADA' in text
        has_forjada = 'FORJADA' in text
        
        if has_forjada:
             result['fabrication'] = 'FORJADA'
             # If ambiguous with others
             if has_trefilada or has_laminada:
                 result['parse_warnings'].append("Ambigüedad: Detectadas FORJADA y otros tipos")
        elif has_trefilada and has_laminada:
            # Ambiguno
            result['parse_warnings'].append("Ambigüedad: Detectadas ambas TREFILADA y LAMINADA")
            result['parse_confidence'] -= 20
        elif has_trefilada:
            result['fabrication'] = 'TREFILADA'
        elif has_laminada:
            result['fabrication'] = 'LAMINADA'
        else:
            # Desconocido
            pass 

        # PASO 3.5 - Compact Format Detection (DxWxL) typical in Forjadas
        # Example: 18 X 82 X 220 -> Diam 18, Width 82, Length 220
        # Format: Number(fraction?) X Number X Number
        # Needs to closely precede or follow? Usually in middle.
        
        # Regex for D x W x L
        # ([\d/]+) \sX\s (\d+) \sX\s (\d+)
        compact_match = re.search(r'([\d/]+)\sX\s(\d+)\sX\s(\d+)', text)
        
        if compact_match:
            # Compact match found, likely forjada style
            result['diameter'] = compact_match.group(1)
            result['width'] = int(compact_match.group(2))
            result['length'] = int(compact_match.group(3))
            
            # Skip standard steps 4 & 5 if we found this strong match
        else:
            # PASO 4 – Detectar diámetro (Classic "DE ...")
            # Regla: El diámetro siempre viene después de la palabra DE
            # Buscar DE, leer token siguiente.
            match_diam = re.search(r'\bDE\s+([\d/]+|\d+)', text)
            if match_diam:
                val = match_diam.group(1)
                result['diameter'] = val
            
            # PASO 5 – Detectar ancho y largo
            # Buscar todas las ocurrencias del patrón: X <número>
            matches_dims = re.findall(r'\sX\s(\d+)', text)
            
            if len(matches_dims) >= 1:
                # El primer número encontrado -> ancho
                result['width'] = int(matches_dims[0])
                
                if len(matches_dims) >= 2:
                    # El segundo número encontrado -> largo
                    result['length'] = int(matches_dims[1])
                else:
                    # Solo uno
                    result['parse_warnings'].append("Falta Largo (solo se encontró una medida X)")
            else:
                # Ninguno
                pass

        # PASO 6 – Detectar tipo (forma)
        # Prioridad: SEMICURVA > CURVA > PLANA
        if 'SEMICURVA' in text:
            result['shape'] = 'SEMICURVA'
        elif 'CURVA' in text:
             # Check if it was part of Semicurva (already normalized so S/CURVA is SEMICURVA)
             # Basic check to ensure we don't double match if the logic was weak, but if/elif handles priority
             result['shape'] = 'CURVA'
        elif 'PLANA' in text:
            result['shape'] = 'PLANA'
        # else None

        # PASO 7 – Validar coherencia
        # Marcar campos faltantes
        if not result['fabrication']:
             result['parse_warnings'].append("Falta: Fabricación")
        if not result['diameter']:
             result['parse_warnings'].append("Falta: Diámetro")
             result['parse_warnings'].append("Falta: Diámetro")
        if not result['width']:
             result['parse_warnings'].append("Falta: Ancho")
        if not result['shape']:
             result['parse_warnings'].append("Falta: Forma")

        # Ajustar confianza
        if result['parse_warnings']:
            # Simple penalty logic
            result['parse_confidence'] -= (len(result['parse_warnings']) * 10)
            if result['parse_confidence'] < 0:
                result['parse_confidence'] = 0

        # PASO 8 – Uso para filtros
        # Los datos estructurados result['fabrication'], etc. se usarán para el modelo.
        
        return result
