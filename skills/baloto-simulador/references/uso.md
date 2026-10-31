# Uso reproducible

## Carrito desde un archivo

Guarda fuera del repositorio un JSON con este esquema. Los números siguientes son datos sintéticos de prueba, no una recomendación:

```json
{
  "label": "Mi prueba",
  "budget": 20,
  "draw_count": 3,
  "interval_days": 1,
  "lines": "01 02 03 04 05 | 06\n07 08 09 10 11 | 12"
}
```

El costo es `cantidad de líneas × draw_count`, a un crédito por línea y sorteo. Se permite hasta 500 líneas por experimento y hasta 5.000 participaciones totales. La primera fecha de sorteo es un día después de crear el carrito, en UTC; las siguientes respetan `interval_days`. Los resultados quedan disponibles un día después de cada fecha. La aprobación no modifica las fechas; aprobar un borrador vencido procesa los sorteos que ya correspondan.

```sh
python3 scripts/demo.py --prepare /ruta/entrada.json --port 8765
```

Este comando prepara el carrito y arranca el servidor. Imprime su URL de seguimiento; deja la confirmación ficticia a la persona. Si el puerto no está disponible, aborta antes de crear el carrito. Ejecutarlo de nuevo crea otro experimento: no es una operación idempotente. Para recuperar uno existente, arranca sin `--prepare` y abre su URL o la lista en la portada.

## Servidor y reloj

```sh
python3 scripts/demo.py --data-dir /ruta/privada --port 8765 --poll-seconds 30
```

Las consultas son locales. No hay llamadas a servicios de apuestas ni descarga de resultados reales. El trabajador usa el reloj UTC más el adelanto específico del experimento. Cada resultado se deriva de una clave privada aleatoria y el identificador del sorteo, y se persiste una sola vez. Repetir la consulta no suma créditos de nuevo.

El tablero distingue límite inicial, costo simulado, créditos devueltos y balance de la simulación. El saldo es el límite menos el costo más los créditos devueltos; no representa dinero. Un carrito sin confirmar no descuenta créditos. Las tasas de premios son inventadas para probar ramas del flujo.

## Persistencia y privacidad

La aplicación conserva un único `state.json` mediante reemplazo atómico. Un bloqueo impide dos servidores sobre la misma carpeta. Si un cierre forzado deja `.server.lock`, comprueba que el proceso indicado ya no existe antes de retirar ese archivo. No borres el estado para resolver un bloqueo.

Los ejemplos no contienen datos de personas. No añadas archivos de entrada, el estado, exportaciones, capturas de datos particulares, cookies o credenciales a Git. La carpeta de instalación solo contiene código e instrucciones.

## Comprobación del agente

1. Comprueba `/api/health` y abre la portada con el navegador disponible.
2. Crea un carrito de prueba y verifica el costo esperado.
3. Confírmalo, avanza dos días y comprueba el primer resultado.
4. Repite la comprobación y verifica que los créditos no se duplican.
5. Reinicia con la misma carpeta y verifica que la URL conserva el estado.

Detén con `Ctrl+C`. El seguimiento automático se pausa mientras el proceso no esté activo y se recupera al reiniciar. El paquete no instala servicios de sistema ni crea tareas en GitHub.
