# Resumen de cierre — auditoría de ingestión

13 de octubre de 2026. **Trabajo detenido por petición del usuario. La mejora sustancial del pipeline no está demostrada.**

En la comparación fija de 200 modelos, el control tardó **234,05 s**. El mejor ensayo temporal (`dispatch`) tardó **230,20 s: 1,64% menos**. La última combinación de cambios locales (`lean`) tardó **266,70 s: 13,95% más**. Ninguno alcanzó el criterio de mejora del 20% adoptado para este trabajo. No se presenta el objetivo como completado.

Se verificaron los 200 originales, las mismas nueve columnas de metadatos en los 200 archivos, 192 capturas idénticas byte a byte y los mismos ocho fallos de fuentes inválidas en todas las variantes terminadas. En cada variante, los 192 modelos con captura se confirmaron antes de que su captura estuviera lista. Las capturas continúan como derivados en background mediante DBOS.

## Qué queda conservado

- Estimación abreviada para STL pequeños y ordenación exacta más barata de pares de índices OBJ.
- Consultas de cancelación con autoridad fresca y menor envoltura ORM.
- Preparación del paquete separada de la admisión GPU; contexto de render mínimo y menos imports en el parser.
- Agrupación de avisos al reconciliador: 964 → 762 pasadas en el ensayo correspondiente. **Se mantiene por instrucción del usuario**, aunque no haya acelerado sustancialmente el lote.
- Pruebas escritas y documentación de estos comportamientos; falta validar el estado final.

El dispatch directo y la concurrencia de ingestión cuatro fueron experimentos en imágenes temporales; no están incorporados al diff del repositorio. La alternativa de notificar admisión con una condición local se retiró antes del cierre por falta de beneficio.

## Estado de entrega

| Comprobación | Estado |
| --- | --- |
| Benchmark de 200 modelos con tiempos desglosados | ✅ Terminado; mejora sustancial no alcanzada |
| Paridad del corpus y modelo confirmado antes de captura | ✅ Verificada en los registros de las seis variantes |
| Tests del diff final | ⏭️ Pendientes; no se lanzaron después del requisito de rendimiento |
| Commit, push y CI del diff final | ⏭️ Pendientes; trabajo detenido por el usuario |

Repositorio: `/home/local/PrintStash-gpu-ingestion`.

Rama: `feat/transparent-gpu-ingestion`.

HEAD: `4d60b9f14457e5bd7904e1dd5631b22503ecbd5e`.

PR existente: [#430](https://github.com/xiao-villamor/PrintStash/pull/430), sin actualizar con estos cambios locales. Su CI anterior no valida el diff pendiente.

Las ejecuciones de auditoría están cerradas. Los cambios locales permanecen sin commit ni push y no se han desplegado. No se continúa automáticamente.

[Reporte final, desglose completo y matriz de comportamiento](compute-ingestion-audit.es.md).

Evidencia consolidada: `/tmp/printstash-ingestion-200-20261013/final-analysis.json`; registros por variante en ese mismo directorio. Los artefactos temporales no están versionados.
