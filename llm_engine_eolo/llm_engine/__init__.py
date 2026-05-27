"""
EOLO Crop LLM Engine
====================
Servicio LLM que toma decisiones de Theta Harvest basadas en el KB de Juan.

Arquitectura:
- FastAPI service expuesto en /decide
- KB cargado desde Excel al startup
- Claude Sonnet 4.6 como motor de razonamiento
- Safety rails para paper trading
"""
__version__ = "0.1.0"
