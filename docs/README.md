# Documentación de Compra Óptima

Documentación de ingeniería. Está pensada para que alguien que no vio nada del proyecto pueda entender
cómo funciona, por qué está hecho así, cómo operarlo y cómo cambiarlo sin romperlo.

| Documento | Para qué leerlo |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | **Empezá acá.** Mapa del sistema, ciclo de una consulta, módulos, datos, despliegue, principios y modos de falla |
| [AI.md](AI.md) | La capa de IA: proveedores, reparto de tareas, cuotas, **presupuesto de tokens medido**, técnicas de ahorro y **catálogo de prompts** |
| [SEARCH.md](SEARCH.md) | De "quiero un mouse" a la recomendación: entrevista, búsqueda, prefiltro, evaluación, disponibilidad, rangos |
| [STORES.md](STORES.md) | De dónde salen los precios: plataformas, tiendas, particularidades de Frávega, Mercado Libre vía ComparaYa, reglas |
| [FOLLOW.md](FOLLOW.md) | Seguir precio: Telegram, escaneo programado y qué es "bajar de verdad" |
| [OPERATIONS.md](OPERATIONS.md) | Variables de entorno, despliegue, monitoreo y **runbook** de fallas |
| [DECISIONS.md](DECISIONS.md) | Registro de decisiones de arquitectura (el porqué de cada elección) |
| [PRUEBAS.md](PRUEBAS.md) | **Plan de pruebas manuales:** casos, qué escribir, qué debería mostrar y señales de problema |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Cómo trabajar en el proyecto: convenciones, tests, documentación |
| [../CLAUDE.md](../CLAUDE.md) | Instrucciones para las sesiones de Claude Code que trabajan en el repo |

## Regla de oro

**La documentación se actualiza en el mismo commit que el cambio.** Un cambio de comportamiento, de prompt, de
tienda, de límite o de configuración sin su documento es un cambio incompleto. Detalle en
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Qué dato va dónde

| Si cambiaste… | Actualizá… |
|---|---|
| un prompt, una ruta, un modelo o un límite de IA | [AI.md](AI.md) (y `config/ai_limits.json` si es un límite) |
| cómo se busca, se filtra, se evalúa o se agrupa | [SEARCH.md](SEARCH.md) |
| una tienda, una plataforma o Mercado Libre | [STORES.md](STORES.md) |
| Seguir precio, avisos o el escaneo | [FOLLOW.md](FOLLOW.md) |
| una variable, un secret o el despliegue | [OPERATIONS.md](OPERATIONS.md) |
| algo que condiciona el diseño o revierte una decisión | [DECISIONS.md](DECISIONS.md) (nuevo ADR) |
| un módulo, una tabla o un endpoint | [ARCHITECTURE.md](ARCHITECTURE.md) |
| un flujo o una pantalla que ve la persona | [PRUEBAS.md](PRUEBAS.md) (el caso de prueba que lo cubre) |
