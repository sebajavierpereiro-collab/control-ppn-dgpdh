"""
diagnostico_login.py — Ayuda cuando alguien no puede ingresar.

    python diagnostico_login.py                       muestra qué base usa, los usuarios y los bloqueos
    python diagnostico_login.py usuario@ppn.gov.ar    además prueba claves típicas para ese usuario
    python diagnostico_login.py usuario@ppn.gov.ar NuevaClave1   cambia su clave (queda provisoria) y lo desbloquea

Hay que correrlo DESDE LA MISMA CARPETA desde la que se arranca la app: cada carpeta tiene su propia base.
"""
import sys

import auth
import database as db


def main():
    print("Base de datos que usa esta carpeta:", db.RUTA_DB)
    if not db.RUTA_DB.exists():
        print("  -> NO EXISTE. Esta carpeta todavía no tiene base (la app no se arrancó acá).")
        return
    db.inicializar_db()
    usuarios = db.listar_usuarios()
    print(f"Usuarios cargados: {len(usuarios)} (activos: {sum(1 for u in usuarios if u['activo'])})")
    director = db.obtener_usuario_por_nombre("director")
    print("Usuario 'director':", "existe" if director else "no existe",
          "| debe cambiar clave:" , bool(director and director['debe_cambiar_clave']) if director else "-")
    fallidos = db.listar_intentos_fallidos(200)
    por_usuario = {}
    for f in fallidos:
        por_usuario[f["usuario"]] = por_usuario.get(f["usuario"], 0) + 1
    if por_usuario:
        print("Intentos fallidos recientes (usuario: cantidad):")
        for u, c in sorted(por_usuario.items(), key=lambda x: -x[1])[:15]:
            print(f"  {u}: {c}  {'<- BLOQUEADO' if db.login_bloqueado(u, '') else ''}")
    if len(sys.argv) < 2:
        return

    nombre = sys.argv[1].strip()
    u = db.obtener_usuario_por_nombre(nombre) or db.obtener_usuario_por_nombre(nombre.lower())
    if not u:
        parecidos = [x["usuario"] for x in usuarios if nombre.lower().split("@")[0] in x["usuario"].lower()][:5]
        print(f"El usuario '{nombre}' NO existe en esta base. Parecidos: {parecidos}")
        return
    print(f"Usuario encontrado: {u['usuario']} | rol {u['rol']} | activo: {bool(u['activo'])} | clave provisoria: {bool(u['debe_cambiar_clave'])}")
    print("Bloqueado por intentos fallidos:", db.login_bloqueado(u["usuario"], ""))

    if len(sys.argv) >= 3:
        nueva = sys.argv[2]
        error = auth.validar_clave_nueva(nueva, u["usuario"]) if nueva != "12345678" else None
        if error:
            print("Clave no aceptada:", error)
            return
        h, s = auth.generar_hash_clave(nueva)
        db.cambiar_clave_usuario(u["id"], h, s, debe_cambiar=True)
        with db._conexion() as con:
            con.execute("DELETE FROM intentos_login WHERE usuario = ?", (u["usuario"].lower(),))
        print(f"Listo: la clave de {u['usuario']} es ahora la indicada (provisoria) y se quitaron sus bloqueos.")
    else:
        for prueba in ("12345678", "director123"):
            if auth.verificar_clave(prueba, u["clave_hash"], u["clave_salt"]):
                print(f"La clave actual de este usuario ES '{prueba}'.")
                break
        else:
            print("Su clave NO es 12345678 ni director123 (ya la cambió o fue reseteada).")


if __name__ == "__main__":
    main()
