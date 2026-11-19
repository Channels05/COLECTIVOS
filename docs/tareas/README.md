# Tareas para Copilot

Trabajo repartido: **Claude** diseña cada tarea y revisa el resultado; **Copilot** (VS Code, modo
agente) la implementa; **Felipe** decide qué se hace y aprueba lo que se sube.

## El ciclo

1. **Claude escribe la tarea** en esta carpeta (`NNN-nombre.md`): qué hacer, en qué archivos, qué
   no tocar y cómo se comprueba que quedó bien.
2. **Felipe se la pasa a Copilot**: abre la carpeta `backend` en VS Code, abre el chat de
   Copilot en modo **Agent** y escribe:

   > Haz la tarea `docs/tareas/NNN-nombre.md`. Sigue `.github/copilot-instructions.md`. Al terminar,
   > completa la sección "Resultado" de la tarea.

3. **Felipe le avisa a Claude** ("Copilot terminó la tarea NNN"). Claude revisa los cambios en la
   carpeta (sin que se hayan subido): lee el diff, corre los tests, prueba lo que corresponda y
   anota la revisión al final de la tarea.
4. Si hay correcciones, Felipe le pasa a Copilot la sección "Revisión" y se repite el paso 3.
5. Cuando está bien, Claude hace el commit y lo sube (y publica la app si corresponde).

## Estado de una tarea

Al comienzo de cada archivo: `Estado: pendiente | en curso (Copilot) | en revisión (Claude) |
cambios pedidos | lista`.
