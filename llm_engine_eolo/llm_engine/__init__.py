"""
EOLO Crop LLM Engine
====================
Servicio LLM que toma decisiones de Theta Harvest basadas en el KB de Juan.

Arquitectura:
- FastAPI service expuesto en /decide y /pre_decide
- KB cargado desde Excel al startup
- Claude Sonnet 4.6 como motor de razonamiento (full /decide)
- Claude Haiku 4.5 como pre-filtro (layered v0.2 /pre_decide)
- Safety rails para paper trading
"""
__version__ = "0.2.0"

from llm_engine.decision_parser import Decision
from llm_engine.haiku_prefilter import PreDecision

__all__ = ["Decision", "PreDecision"]
