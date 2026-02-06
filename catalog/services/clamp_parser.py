
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
        }
        
        for k, v in replacements.items():
            text = text.replace(k, v)
            
        # 3. Espacios duplicados
        text = re.sub(r'\s+', ' ', text)
        
        # 4. Asegurar separadores claros para X (dimensiones)
        # "todo X rodeado de espacios -> X"
        # Regex: cualquier espacio (o nada) seguido de X seguido de cualquier espacio (o nada)
        # Se reemplaza por " X "
        # Solo si la X parece ser un separador (está entre otras cosas, o dígitos)
        # Para ser seguros segun requerimiento: "todo X rodeado de espacios"
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
        
        if has_trefilada and has_laminada:
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

        # PASO 4 – Detectar diámetro
        # Regla: El diámetro siempre viene después de la palabra DE
        # Buscar DE, leer token siguiente.
        match_diam = re.search(r'\bDE\s+([\d/]+|\d+)', text)
        if match_diam:
            val = match_diam.group(1)
            # Validar si es numero o fraccion (simple check)
            if re.match(r'^[\d/]+$', val):
                 result['diameter'] = val
            else:
                 result['parse_warnings'].append(f"Diámetro inválido detectado: {val}")
        else:
            result['diameter'] = None # Desconocido

        # PASO 5 – Detectar ancho y largo
        # Buscar todas las ocurrencias del patrón: X <número>
        # (\sX\s)(\d+) -> el espacio ya fue normalizado a " X "
        
        # Find all " X number" patterns
        # Note: \b is word boundary.
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
