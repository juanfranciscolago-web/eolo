# ============================================================
#  eolo_common — código compartido entre los 4 Eolos
#
#  Sub-paquetes:
#    multi_tf/   — buffer de velas 1min + resample + confluencia
#                  Usado por v1, v1.2, v2 (opciones) y crypto
#                  para que todos trabajen multi-timeframe de la
#                  misma forma (1min base + resample in-memory).
#
#  Este paquete se instala implícitamente en cada imagen Docker
#  via `COPY eolo_common/ ./eolo_common/` desde el repo root.
#  No se publica como pip package — los 4 Eolos viven en el mismo
#  monorepo y comparten el código copiándolo al build.
# ============================================================
__version__ = "1.0.0"
