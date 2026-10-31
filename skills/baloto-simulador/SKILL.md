---
name: baloto-simulador
description: Crea una demostración local de registro de números, carrito ficticio y seguimiento de sorteos simulados para aprender a construir skills. Usar para demos y pruebas educativas; no opera apuestas, cuentas ni pagos reales.
---

# Simulación educativa de un flujo de lotería

Esta skill ejecuta una aplicación local, con créditos sin valor monetario y resultados sintéticos. Es independiente de Baloto y no requiere cuentas, credenciales, conexión a su web ni herramientas específicas de un agente.

## Conversación inicial

Reutiliza las respuestas que ya dio la persona. Pregunta solamente lo que falta, agrupando preguntas sencillas:

1. ¿Qué números de ejemplo desea cargar? Una línea por combinación: `01 02 03 04 05 | 06` (cinco números distintos entre 1 y 43 y uno entre 1 y 16).
2. ¿Cuál es el límite de **créditos ficticios**, cuántos sorteos simulados y cada cuántos días? Cada línea cuesta un crédito por sorteo. Las mismas líneas se repiten durante el experimento.
3. ¿Quiere recorrer el flujo en el navegador o que el agente prepare el carrito demo con esas respuestas? ¿Prefiere esperar el reloj o avanzar días simulados para comprobar el seguimiento?

No propongas números ni estrategias para ganar. La estrategia de esta demo es operativa: validar las líneas recibidas, comprobar que su costo cabe en el límite, preparar el carrito y seguir su resultado. Si excede el límite, muestra el costo y solicita ajustar la configuración; no cambies cantidades en silencio.

## Ejecutar

Usa la ruta real del directorio de esta skill; no presupongas una carpeta personal ni un sistema operativo. Requiere Python 3.10 o superior, sin paquetes adicionales.

```sh
python3 scripts/demo.py --port 8765
```

En Windows se puede usar `py -3`. El servidor muestra su URL al arrancar. Conserva la sesión del proceso o explica cómo mantenerlo abierto. Solo anuncia una URL funcional después de comprobar una respuesta HTTP. Si el puerto está ocupado, usa otro y entrega la URL efectiva; no detengas procesos ajenos.

Los datos se guardan fuera del código, por defecto en `~/.local/share/baloto-simulador`. Para pruebas aisladas, pasa `--data-dir /ruta/temporal`. Nunca guardes entradas de usuarios ni exportaciones en el repositorio público.

## Recorrer el flujo

1. Abre la URL local y usa la sesión de demostración. No es una sesión autenticada de un operador.
2. Carga las líneas proporcionadas, el límite de créditos, la cantidad de sorteos y el intervalo. Los duplicados se rechazan para evitar cargar la misma línea por error.
3. Pulsa **Preparar carrito demo**. Comprueba el resumen de líneas, sorteos, créditos usados y créditos disponibles.
4. Entrega la URL `/seguimiento/<id>`. La persona puede dejar el carrito en borrador o pulsar **Confirmar simulación**. Esta acción solo cambia el estado local; no cobra ni genera un tiquete válido.
5. El trabajador incorporado revisa cada 30 segundos si ya pasó un día desde cada sorteo simulado. Entonces genera un resultado sintético reproducible y calcula créditos de demostración con una tabla inventada, visible en el tablero.
6. **Avanzar un día** permite probar el flujo sin esperar. Solo modifica el reloj de ese experimento. Un borrador no produce resultados.

El proceso debe seguir ejecutándose para actualizar automáticamente. Al reiniciarlo con la misma carpeta recupera los experimentos confirmados y procesa los vencidos. No instales servicios de arranque ni tareas programadas salvo que la persona lo pida; crear una skill no activa un proceso permanente.

Para preparar el carrito desde un archivo y automatizar el trabajo del agente, consulta [references/uso.md](references/uso.md). Para explicar cómo se construyó, relaciona frontmatter (descubrimiento), cuerpo (decisiones), script (operaciones deterministas), pruebas (invariantes) y metadatos opcionales (presentación en Codex).

## Límites de la demostración

- Mantén el servidor en `127.0.0.1`. No es un servicio con autenticación multiusuario ni está diseñado para exponerse a internet.
- Los números introducidos son ejemplos del usuario; el script no recomienda ni compra apuestas.
- No añadas conectores a la web oficial, sesiones reales, carritos reales, pagos, datos de tarjeta ni enlaces de checkout.
- No describas los créditos calculados como premios oficiales, dinero ganado o dinero cobrado. La tabla de premios es ficticia.
- Una prueba técnica demuestra comportamiento del código; no demuestra que otro agente haya descubierto o ejecutado la skill. Informa por separado instalación, pruebas y publicación.

## Entrega al usuario

Da el enlace local real, el resumen del experimento, si quedó en borrador o confirmado, dónde se guardan los datos y cómo detener el proceso. Explica que la URL local funciona en ese equipo mientras el servidor esté activo. No anuncies seguimiento permanente si solo dejaste archivos o instrucciones.
