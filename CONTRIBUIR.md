# Cómo se trabaja con este repositorio

## Regla general
`main` está protegida: nadie sube directo. Cada cambio sigue este camino:

1. **Rama nueva** desde `main`, con un nombre que diga qué hace: `arreglo/despacho-lento`, `mejora/metas-salud`.
2. **Commits chicos**, en castellano, que expliquen qué cambió y por qué.
3. Antes de abrir el pedido: `python herramientas/verificar.py` tiene que terminar en «todo en orden».
4. **Pull request** contra `main`. En la descripción, la sección «Qué cambia en el servidor» (ver abajo).
5. **Revisión** de Sistemas (Rodrigo) y **merge**.

## Versiones
Cada versión que se instala lleva una etiqueta (`v41.3`, `v42`, …) y una entrada en `CAMBIOS.md` que dice **siempre**:

- ¿Cambió `config.ini`? (claves nuevas u obligatorias; `config.ini.ejemplo` se actualiza en el mismo pull request)
- ¿Cambió `requirements.txt`? (hay que correr `pip install -r requirements.txt`)
- ¿Hay migraciones de base nuevas? (se aplican solas al arrancar, después de un respaldo automático)
- ¿Algo del servidor? (nginx, systemd, carpetas, permisos, paquetes del sistema)

## Actualizar el servidor
```
sudo -u dgpdh git -C /opt/dgpdh/dgpdh_web pull
sudo -u dgpdh /opt/dgpdh/venv/bin/pip install -r /opt/dgpdh/dgpdh_web/requirements.txt
sudo systemctl restart dgpdh
```
`datos/` y `config.ini` no se tocan: Git los ignora.

## Qué no se sube nunca
Está todo en `.gitignore`: datos reales, `config.ini`, claves y certificados, respaldos, planillas con personas,
LanguageTool, entornos virtuales y registros. Si algo sensible se sube por error, borrarlo en un commit nuevo **no
alcanza** (queda en el historial): avisar a Sistemas y cambiar ese dato (por ejemplo, generar una clave secreta nueva).
