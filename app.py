from flask import Flask, render_template, request, jsonify, redirect
from sqlalchemy import create_engine, text, event
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone, timedelta
import os, requests as req_lib

app = Flask(__name__)

# ── Base de datos ─────────────────────────────────────────────────────────────
# En la nube, Railway/Render proveen DATABASE_URL con PostgreSQL.
# En local, usamos SQLite automáticamente.

_raw = os.environ.get('DATABASE_URL', '')
if _raw:
    # Railway a veces da "postgres://" pero SQLAlchemy necesita "postgresql://"
    DATABASE_URL = _raw.replace('postgres://', 'postgresql://', 1)
else:
    _path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deposito.db')
    DATABASE_URL = f'sqlite:///{_path}'

IS_PG  = DATABASE_URL.startswith('postgresql')
engine = create_engine(
    DATABASE_URL,
    connect_args={} if IS_PG else {'check_same_thread': False}
)

if not IS_PG:
    @event.listens_for(engine, 'connect')
    def _fk_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA foreign_keys=ON')
        cur.close()


ARG = timezone(timedelta(hours=-3))  # UTC-3 Argentina

def _now():
    return datetime.now(ARG).strftime('%Y-%m-%d %H:%M:%S')


def _row(r):
    return dict(r._mapping)


# ── Esquema ───────────────────────────────────────────────────────────────────

def init_db():
    if IS_PG:
        stmts = [
            """CREATE TABLE IF NOT EXISTS palets (
                id          SERIAL PRIMARY KEY,
                codigo      TEXT UNIQUE NOT NULL,
                descripcion TEXT DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS stock (
                id              SERIAL PRIMARY KEY,
                palet_id        INTEGER NOT NULL REFERENCES palets(id) ON DELETE CASCADE,
                producto        TEXT NOT NULL,
                color           TEXT NOT NULL DEFAULT '',
                cajas           INTEGER NOT NULL DEFAULT 0,
                piezas_por_caja INTEGER NOT NULL DEFAULT 0,
                UNIQUE(palet_id, producto, color)
            )""",
            """CREATE TABLE IF NOT EXISTS movimientos (
                id               SERIAL PRIMARY KEY,
                tipo             TEXT NOT NULL,
                palet_id         INTEGER REFERENCES palets(id),
                palet_destino_id INTEGER REFERENCES palets(id),
                producto         TEXT NOT NULL,
                color            TEXT DEFAULT '',
                cajas            INTEGER NOT NULL,
                observacion      TEXT DEFAULT '',
                fecha            TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS productos (
                id              SERIAL PRIMARY KEY,
                nombre          TEXT NOT NULL,
                sku             TEXT NOT NULL,
                color           TEXT NOT NULL DEFAULT '',
                costo           NUMERIC NOT NULL DEFAULT 0,
                stock_separado  INTEGER NOT NULL DEFAULT 0,
                stock_full      INTEGER NOT NULL DEFAULT 0,
                ventas_mes      INTEGER NOT NULL DEFAULT 0,
                UNIQUE(sku, color)
            )""",
        ]
    else:
        stmts = [
            """CREATE TABLE IF NOT EXISTS palets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo      TEXT UNIQUE NOT NULL COLLATE NOCASE,
                descripcion TEXT DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS stock (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                palet_id        INTEGER NOT NULL,
                producto        TEXT NOT NULL,
                color           TEXT NOT NULL DEFAULT '',
                cajas           INTEGER NOT NULL DEFAULT 0,
                piezas_por_caja INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(palet_id) REFERENCES palets(id) ON DELETE CASCADE,
                UNIQUE(palet_id, producto, color)
            )""",
            """CREATE TABLE IF NOT EXISTS movimientos (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo             TEXT NOT NULL,
                palet_id         INTEGER REFERENCES palets(id),
                palet_destino_id INTEGER REFERENCES palets(id),
                producto         TEXT NOT NULL,
                color            TEXT DEFAULT '',
                cajas            INTEGER NOT NULL,
                observacion      TEXT DEFAULT '',
                fecha            TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS productos (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre         TEXT NOT NULL,
                sku            TEXT NOT NULL COLLATE NOCASE,
                color          TEXT NOT NULL DEFAULT '',
                costo          REAL NOT NULL DEFAULT 0,
                stock_separado INTEGER NOT NULL DEFAULT 0,
                stock_full     INTEGER NOT NULL DEFAULT 0,
                ventas_mes     INTEGER NOT NULL DEFAULT 0,
                UNIQUE(sku, color)
            )""",
        ]

    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))


def migrate_db():
    """Actualiza la base de datos existente para incluir piezas_por_caja en el índice único de stock."""
    try:
        if IS_PG:
            with engine.connect() as conn:
                # Verificar si el constraint ya incluye piezas_por_caja
                already = conn.execute(text("""
                    SELECT 1
                    FROM information_schema.constraint_column_usage
                    WHERE table_name = 'stock'
                      AND column_name = 'piezas_por_caja'
                      AND constraint_name IN (
                          SELECT constraint_name
                          FROM information_schema.table_constraints
                          WHERE table_name = 'stock' AND constraint_type = 'UNIQUE'
                      )
                """)).fetchone()

            if not already:
                with engine.begin() as conn:
                    # Eliminar constraint viejo
                    old = conn.execute(text("""
                        SELECT constraint_name
                        FROM information_schema.table_constraints
                        WHERE table_name = 'stock' AND constraint_type = 'UNIQUE'
                        LIMIT 1
                    """)).fetchone()
                    if old:
                        conn.execute(text(f'ALTER TABLE stock DROP CONSTRAINT "{old[0]}"'))
                    # Agregar constraint nuevo
                    conn.execute(text(
                        'ALTER TABLE stock ADD CONSTRAINT stock_unique_ppk '
                        'UNIQUE (palet_id, producto, color, piezas_por_caja)'
                    ))
                    print('  Migración aplicada: nuevo índice en stock.')
        else:
            # SQLite: verificar si la constraint ya incluye piezas_por_caja
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='stock'"
                )).fetchone()
            if row:
                sql = row[0]
                unique_part = sql[sql.upper().find('UNIQUE'):]
                if 'piezas_por_caja' not in unique_part.lower():
                    # Recrear la tabla con el constraint correcto
                    with engine.begin() as conn:
                        conn.execute(text('ALTER TABLE stock RENAME TO _stock_old'))
                        conn.execute(text('''
                            CREATE TABLE stock (
                                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                                palet_id        INTEGER NOT NULL,
                                producto        TEXT NOT NULL,
                                color           TEXT NOT NULL DEFAULT \'\',
                                cajas           INTEGER NOT NULL DEFAULT 0,
                                piezas_por_caja INTEGER NOT NULL DEFAULT 0,
                                FOREIGN KEY(palet_id) REFERENCES palets(id) ON DELETE CASCADE,
                                UNIQUE(palet_id, producto, color, piezas_por_caja)
                            )
                        '''))
                        conn.execute(text(
                            'INSERT INTO stock SELECT id,palet_id,producto,color,cajas,piezas_por_caja FROM _stock_old'
                        ))
                        conn.execute(text('DROP TABLE _stock_old'))
                    print('  Migración SQLite aplicada: nuevo índice en stock.')
    except Exception as ex:
        print(f'  Aviso migración: {ex}')

    # Migración: columnas de proyecciones en productos
    _nuevas_pg = [
        ('costo',          'NUMERIC NOT NULL DEFAULT 0'),
        ('stock_separado', 'INTEGER NOT NULL DEFAULT 0'),
        ('stock_full',     'INTEGER NOT NULL DEFAULT 0'),
        ('ventas_mes',     'INTEGER NOT NULL DEFAULT 0'),
    ]
    _nuevas_sq = [
        ('costo',          'REAL    NOT NULL DEFAULT 0'),
        ('stock_separado', 'INTEGER NOT NULL DEFAULT 0'),
        ('stock_full',     'INTEGER NOT NULL DEFAULT 0'),
        ('ventas_mes',     'INTEGER NOT NULL DEFAULT 0'),
    ]
    try:
        if IS_PG:
            with engine.begin() as conn:
                for col, tipo in _nuevas_pg:
                    conn.execute(text(
                        f'ALTER TABLE productos ADD COLUMN IF NOT EXISTS {col} {tipo}'
                    ))
        else:
            with engine.connect() as conn:
                cols = [r[1] for r in conn.execute(text('PRAGMA table_info(productos)')).fetchall()]
            for col, tipo in _nuevas_sq:
                if col not in cols:
                    with engine.begin() as conn:
                        conn.execute(text(f'ALTER TABLE productos ADD COLUMN {col} {tipo}'))
                    print(f'  Migración aplicada: columna {col} en productos.')
    except Exception as ex:
        print(f'  Aviso migración proyecciones: {ex}')

    # Tabla ml_config para guardar tokens de ML
    try:
        with engine.begin() as conn:
            if IS_PG:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS ml_config (
                        key   TEXT PRIMARY KEY,
                        value TEXT
                    )
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS ml_config (
                        key   TEXT PRIMARY KEY,
                        value TEXT
                    )
                """))
    except Exception as ex:
        print(f'  Aviso migración ml_config: {ex}')


init_db()
migrate_db()

print(f'\n  Base de datos: {"PostgreSQL ✓" if IS_PG else "SQLite (DATOS SE PIERDEN AL REDEPLOYAR)"}\n')


# ── Rutas ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/mobile')
def mobile():
    return render_template('mobile.html')


@app.route('/api/palets')
def get_palets():
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT p.id, p.codigo, p.descripcion,
                   COALESCE(SUM(s.cajas), 0) AS total_cajas,
                   COUNT(s.id)               AS total_items
            FROM palets p
            LEFT JOIN stock s ON s.palet_id = p.id
            GROUP BY p.id, p.codigo, p.descripcion
            ORDER BY p.codigo
        ''')).fetchall()
    return jsonify([_row(r) for r in rows])


@app.route('/api/palets/full')
def get_palets_full():
    with engine.connect() as conn:
        palets = conn.execute(
            text('SELECT id, codigo, descripcion FROM palets ORDER BY codigo')
        ).fetchall()
        result = []
        for p in palets:
            stock = conn.execute(
                text('SELECT producto, color, cajas, piezas_por_caja FROM stock WHERE palet_id=:pid ORDER BY producto, color'),
                {'pid': p.id}
            ).fetchall()
            d = _row(p)
            d['stock']       = [_row(s) for s in stock]
            d['total_cajas'] = sum(s.cajas for s in stock)
            d['total_items'] = len(stock)
            result.append(d)
    return jsonify(result)


@app.route('/api/palets', methods=['POST'])
def create_palet():
    data   = request.json
    codigo = (data.get('codigo') or '').strip().upper()
    if not codigo:
        return jsonify({'error': 'El código es obligatorio'}), 400
    try:
        with engine.begin() as conn:
            conn.execute(
                text('INSERT INTO palets (codigo, descripcion) VALUES (:c, :d)'),
                {'c': codigo, 'd': (data.get('descripcion') or '').strip()}
            )
        return jsonify({'ok': True})
    except IntegrityError:
        return jsonify({'error': f'Ya existe un palet con el código {codigo}'}), 400


@app.route('/api/palets/<int:pid>', methods=['DELETE'])
def delete_palet(pid):
    with engine.begin() as conn:
        # Desvincular del historial sin perder los registros
        conn.execute(text('UPDATE movimientos SET palet_id=NULL WHERE palet_id=:pid'), {'pid': pid})
        conn.execute(text('UPDATE movimientos SET palet_destino_id=NULL WHERE palet_destino_id=:pid'), {'pid': pid})
        # Borrar stock del palet
        conn.execute(text('DELETE FROM stock WHERE palet_id=:pid'), {'pid': pid})
        # Borrar el palet
        conn.execute(text('DELETE FROM palets WHERE id=:pid'), {'pid': pid})
    return jsonify({'ok': True})


@app.route('/api/palets/<int:pid>/stock')
def get_palet_stock(pid):
    with engine.connect() as conn:
        rows = conn.execute(
            text('SELECT * FROM stock WHERE palet_id=:pid ORDER BY producto, color'),
            {'pid': pid}
        ).fetchall()
    return jsonify([_row(r) for r in rows])


@app.route('/api/movimientos')
def get_movimientos():
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT m.*,
                   p1.codigo AS palet_codigo,
                   p2.codigo AS palet_destino_codigo
            FROM movimientos m
            LEFT JOIN palets p1 ON m.palet_id          = p1.id
            LEFT JOIN palets p2 ON m.palet_destino_id  = p2.id
            ORDER BY m.fecha DESC
            LIMIT 300
        ''')).fetchall()
    return jsonify([_row(r) for r in rows])


@app.route('/api/movimientos', methods=['POST'])
def crear_movimiento():
    d    = request.json
    tipo = d.get('tipo')
    if tipo not in ('ingreso', 'egreso', 'transferencia', 'ajuste'):
        return jsonify({'error': 'Tipo de movimiento inválido'}), 400

    try:
        with engine.begin() as conn:

            if tipo == 'ingreso':
                _add(conn, d['palet_id'], d['producto'], d['color'], d.get('piezas_por_caja', 0), d['cajas'])
                conn.execute(text(
                    'INSERT INTO movimientos (tipo,palet_id,producto,color,cajas,observacion,fecha) '
                    'VALUES (:t,:pid,:prod,:col,:c,:obs,:f)'
                ), {'t': tipo, 'pid': d['palet_id'], 'prod': d['producto'],
                    'col': d['color'], 'c': d['cajas'],
                    'obs': d.get('observacion', ''), 'f': _now()})

            elif tipo == 'egreso':
                ok = _sub(conn, d['palet_id'], d['producto'], d['color'], d['cajas'], d.get('piezas_por_caja', 0))
                if not ok:
                    raise ValueError('Stock insuficiente para ese movimiento')
                conn.execute(text(
                    'INSERT INTO movimientos (tipo,palet_id,producto,color,cajas,observacion,fecha) '
                    'VALUES (:t,:pid,:prod,:col,:c,:obs,:f)'
                ), {'t': tipo, 'pid': d['palet_id'], 'prod': d['producto'],
                    'col': d['color'], 'c': d['cajas'],
                    'obs': d.get('observacion', ''), 'f': _now()})

            elif tipo == 'transferencia':
                dest = d.get('palet_destino_id')
                if not dest or dest == d['palet_id']:
                    raise ValueError('El palet origen y destino no pueden ser el mismo')
                row = conn.execute(
                    text('SELECT piezas_por_caja FROM stock WHERE palet_id=:pid AND producto=:prod AND color=:col'),
                    {'pid': d['palet_id'], 'prod': d['producto'], 'col': d['color']}
                ).fetchone()
                ppk = row.piezas_por_caja if row else 0
                ok  = _sub(conn, d['palet_id'], d['producto'], d['color'], d['cajas'], ppk)
                if not ok:
                    raise ValueError('Stock insuficiente en el palet origen')
                _add(conn, dest, d['producto'], d['color'], ppk, d['cajas'])
                conn.execute(text(
                    'INSERT INTO movimientos (tipo,palet_id,palet_destino_id,producto,color,cajas,observacion,fecha) '
                    'VALUES (:t,:pid,:dest,:prod,:col,:c,:obs,:f)'
                ), {'t': tipo, 'pid': d['palet_id'], 'dest': dest,
                    'prod': d['producto'], 'col': d['color'], 'c': d['cajas'],
                    'obs': d.get('observacion', ''), 'f': _now()})

            elif tipo == 'ajuste':
                _set(conn, d['palet_id'], d['producto'], d['color'], d.get('piezas_por_caja', 0), d['cajas'])
                conn.execute(text(
                    'INSERT INTO movimientos (tipo,palet_id,producto,color,cajas,observacion,fecha) '
                    'VALUES (:t,:pid,:prod,:col,:c,:obs,:f)'
                ), {'t': tipo, 'pid': d['palet_id'], 'prod': d['producto'],
                    'col': d['color'], 'c': d['cajas'],
                    'obs': d.get('observacion', ''), 'f': _now()})

        return jsonify({'ok': True})

    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception as ex:
        return jsonify({'error': str(ex)}), 500


# ── Helpers de stock ──────────────────────────────────────────────────────────

def _add(conn, palet_id, producto, color, ppk, cajas):
    # Busca por producto + color + piezas_por_caja: cada combinación es una entrada independiente
    row = conn.execute(
        text('SELECT id FROM stock WHERE palet_id=:pid AND producto=:prod AND color=:col AND piezas_por_caja=:ppk'),
        {'pid': palet_id, 'prod': producto, 'col': color, 'ppk': ppk}
    ).fetchone()
    if row:
        conn.execute(
            text('UPDATE stock SET cajas=cajas+:c WHERE id=:id'),
            {'c': cajas, 'id': row.id}
        )
    else:
        conn.execute(
            text('INSERT INTO stock (palet_id,producto,color,cajas,piezas_por_caja) VALUES (:pid,:prod,:col,:c,:ppk)'),
            {'pid': palet_id, 'prod': producto, 'col': color, 'c': cajas, 'ppk': ppk}
        )


def _sub(conn, palet_id, producto, color, cajas, ppk=0):
    # Si se especifica ppk busca exacto; si no, toma cualquier entrada del producto
    if ppk:
        q = 'SELECT id, cajas FROM stock WHERE palet_id=:pid AND producto=:prod AND color=:col AND piezas_por_caja=:ppk'
        params = {'pid': palet_id, 'prod': producto, 'col': color, 'ppk': ppk}
    else:
        q = 'SELECT id, cajas FROM stock WHERE palet_id=:pid AND producto=:prod AND color=:col ORDER BY id LIMIT 1'
        params = {'pid': palet_id, 'prod': producto, 'col': color}
    row = conn.execute(text(q), params).fetchone()
    if not row or row.cajas < cajas:
        return False
    nuevas = row.cajas - cajas
    if nuevas == 0:
        conn.execute(text('DELETE FROM stock WHERE id=:id'), {'id': row.id})
    else:
        conn.execute(text('UPDATE stock SET cajas=:c WHERE id=:id'), {'c': nuevas, 'id': row.id})
    return True


def _set(conn, palet_id, producto, color, ppk, cajas):
    row = conn.execute(
        text('SELECT id FROM stock WHERE palet_id=:pid AND producto=:prod AND color=:col AND piezas_por_caja=:ppk'),
        {'pid': palet_id, 'prod': producto, 'col': color, 'ppk': ppk}
    ).fetchone()
    if cajas == 0:
        if row:
            conn.execute(text('DELETE FROM stock WHERE id=:id'), {'id': row.id})
    elif row:
        conn.execute(
            text('UPDATE stock SET cajas=:c WHERE id=:id'),
            {'c': cajas, 'id': row.id}
        )
    else:
        conn.execute(
            text('INSERT INTO stock (palet_id,producto,color,cajas,piezas_por_caja) VALUES (:pid,:prod,:col,:c,:ppk)'),
            {'pid': palet_id, 'prod': producto, 'col': color, 'c': cajas, 'ppk': ppk}
        )


# ── Catálogo de productos ─────────────────────────────────────────────────────

@app.route('/api/productos')
def get_productos():
    with engine.connect() as conn:
        rows = conn.execute(
            text('SELECT * FROM productos ORDER BY nombre, color')
        ).fetchall()
    return jsonify([_row(r) for r in rows])


@app.route('/api/productos', methods=['POST'])
def create_producto():
    data   = request.json
    nombre = (data.get('nombre') or '').strip()
    sku    = (data.get('sku') or '').strip().upper()
    color  = (data.get('color') or '').strip()
    if not nombre or not sku:
        return jsonify({'error': 'Nombre y SKU son obligatorios'}), 400
    try:
        with engine.begin() as conn:
            conn.execute(
                text('INSERT INTO productos (nombre, sku, color) VALUES (:n, :s, :c)'),
                {'n': nombre, 's': sku, 'c': color}
            )
        return jsonify({'ok': True})
    except IntegrityError:
        return jsonify({'error': f'Ya existe ese SKU con ese color'}), 400


@app.route('/api/productos/<int:pid>', methods=['PUT'])
def update_producto(pid):
    data   = request.json
    nombre = (data.get('nombre') or '').strip()
    sku    = (data.get('sku') or '').strip().upper()
    color  = (data.get('color') or '').strip()
    if not nombre or not sku:
        return jsonify({'error': 'Nombre y SKU son obligatorios'}), 400
    try:
        with engine.begin() as conn:
            conn.execute(
                text('UPDATE productos SET nombre=:n, sku=:s, color=:c WHERE id=:id'),
                {'n': nombre, 's': sku, 'c': color, 'id': pid}
            )
        return jsonify({'ok': True})
    except IntegrityError:
        return jsonify({'error': 'Ya existe ese SKU con ese color'}), 400


@app.route('/api/productos/<int:pid>', methods=['DELETE'])
def delete_producto(pid):
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM productos WHERE id=:pid'), {'pid': pid})
    return jsonify({'ok': True})


@app.route('/api/productos/<int:pid>/proyeccion', methods=['PATCH'])
def update_proyeccion(pid):
    data = request.json
    campos = {}
    for campo in ('stock_separado', 'stock_full', 'ventas_mes'):
        if campo in data:
            try:
                campos[campo] = max(0, int(data[campo]))
            except (TypeError, ValueError):
                return jsonify({'error': f'{campo} inválido'}), 400
    if not campos:
        return jsonify({'error': 'No hay campos para actualizar'}), 400
    set_clause = ', '.join(f'{k}=:{k}' for k in campos)
    campos['id'] = pid
    with engine.begin() as conn:
        conn.execute(text(f'UPDATE productos SET {set_clause} WHERE id=:id'), campos)
    return jsonify({'ok': True})


@app.route('/api/proyecciones')
def get_proyecciones():
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT
                pr.id, pr.nombre, pr.sku, pr.color,
                pr.stock_separado, pr.stock_full, pr.ventas_mes,
                COALESCE(SUM(s.cajas * s.piezas_por_caja), 0) AS stock_deposito
            FROM productos pr
            LEFT JOIN stock s
                   ON LOWER(s.producto) = LOWER(pr.nombre)
                  AND LOWER(s.color)    = LOWER(pr.color)
            GROUP BY pr.id, pr.nombre, pr.sku, pr.color,
                     pr.stock_separado, pr.stock_full, pr.ventas_mes
            ORDER BY pr.nombre, pr.color
        ''')).fetchall()
    return jsonify([_row(r) for r in rows])


@app.route('/api/productos/<int:pid>/costo', methods=['PATCH'])
def update_costo(pid):
    data = request.json
    try:
        costo = float(data.get('costo', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Costo inválido'}), 400
    with engine.begin() as conn:
        conn.execute(
            text('UPDATE productos SET costo=:c WHERE id=:id'),
            {'c': costo, 'id': pid}
        )
    return jsonify({'ok': True})


@app.route('/api/inventario')
def get_inventario():
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT
                s.producto,
                s.color,
                COALESCE(pr.id, 0)    AS prod_id,
                COALESCE(pr.sku, '')  AS sku,
                COALESCE(pr.costo, 0) AS costo,
                SUM(s.cajas)                     AS total_cajas,
                SUM(s.cajas * s.piezas_por_caja) AS total_piezas
            FROM stock s
            LEFT JOIN productos pr
                   ON LOWER(pr.nombre) = LOWER(s.producto)
                  AND LOWER(pr.color)  = LOWER(s.color)
            GROUP BY s.producto, s.color, pr.id, pr.sku, pr.costo
            ORDER BY s.producto, s.color
        ''')).fetchall()
    return jsonify([_row(r) for r in rows])


@app.route('/api/buscar')
def buscar():
    q = request.args.get('q', '').strip().lower()
    if not q:
        return jsonify([])
    like = f'%{q}%'
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT s.producto, s.color, s.cajas, s.piezas_por_caja,
                   p.codigo  AS palet_codigo,
                   p.descripcion AS palet_desc,
                   pr.sku
            FROM stock s
            JOIN palets p ON s.palet_id = p.id
            LEFT JOIN productos pr ON LOWER(pr.nombre) = LOWER(s.producto)
                                  AND LOWER(pr.color)  = LOWER(s.color)
            WHERE LOWER(s.producto) LIKE :q
               OR LOWER(s.color)    LIKE :q
               OR LOWER(COALESCE(pr.sku, '')) LIKE :q
            ORDER BY s.producto, s.color, p.codigo
        '''), {'q': like}).fetchall()
    return jsonify([_row(r) for r in rows])


@app.route('/api/reset', methods=['DELETE'])
def reset_todo():
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM movimientos'))
        conn.execute(text('DELETE FROM stock'))
        conn.execute(text('DELETE FROM palets'))
    return jsonify({'ok': True})


# ── Mercado Libre ─────────────────────────────────────────────────────────────
ML_CLIENT_ID     = os.environ.get('ML_CLIENT_ID', '8410291723054980')
ML_CLIENT_SECRET = os.environ.get('ML_CLIENT_SECRET', 'T1aVi99e0NSgdjHjGQQiwBWho3X6gImM')
ML_REDIRECT_URI  = 'https://deposito-app-production.up.railway.app/ml/callback'
ML_TOKEN_URL     = 'https://api.mercadolibre.com/oauth/token'

# Tokens en memoria (se pierden al reiniciar, pero se renuevan solos)
_ml_tokens = {'access': None, 'refresh': None, 'expires_at': 0}

def ml_get_token_from_code(code):
    r = req_lib.post(ML_TOKEN_URL, data={
        'grant_type': 'authorization_code',
        'client_id': ML_CLIENT_ID,
        'client_secret': ML_CLIENT_SECRET,
        'code': code,
        'redirect_uri': ML_REDIRECT_URI,
    })
    data = r.json()
    _ml_tokens['access']     = data.get('access_token')
    _ml_tokens['refresh']    = data.get('refresh_token')
    _ml_tokens['expires_at'] = datetime.now().timestamp() + data.get('expires_in', 21600) - 300
    # Guardar refresh token en DB para sobrevivir reinicios
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ml_config (key, value) VALUES ('refresh_token', :v)
            ON CONFLICT (key) DO UPDATE SET value=:v
        """), {'v': _ml_tokens['refresh']})
    return _ml_tokens['access']

def ml_refresh():
    refresh = _ml_tokens.get('refresh')
    if not refresh:
        # Intentar cargar desde DB
        try:
            with engine.connect() as conn:
                row = conn.execute(text("SELECT value FROM ml_config WHERE key='refresh_token'")).fetchone()
                if row:
                    refresh = row[0]
                    _ml_tokens['refresh'] = refresh
        except Exception:
            pass
    if not refresh:
        return None
    r = req_lib.post(ML_TOKEN_URL, data={
        'grant_type': 'refresh_token',
        'client_id': ML_CLIENT_ID,
        'client_secret': ML_CLIENT_SECRET,
        'refresh_token': refresh,
    })
    data = r.json()
    _ml_tokens['access']     = data.get('access_token')
    _ml_tokens['refresh']    = data.get('refresh_token')
    _ml_tokens['expires_at'] = datetime.now().timestamp() + data.get('expires_in', 21600) - 300
    if _ml_tokens['refresh']:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO ml_config (key, value) VALUES ('refresh_token', :v)
                ON CONFLICT (key) DO UPDATE SET value=:v
            """), {'v': _ml_tokens['refresh']})
    return _ml_tokens['access']

def ml_token():
    if _ml_tokens['access'] and datetime.now().timestamp() < _ml_tokens['expires_at']:
        return _ml_tokens['access']
    return ml_refresh()

@app.route('/ml/callback')
def ml_callback():
    code = request.args.get('code')
    if not code:
        return 'Error: no se recibió código de ML.', 400
    try:
        ml_get_token_from_code(code)
        return redirect('/?ml=ok')
    except Exception as e:
        return f'Error al obtener token: {e}', 500

@app.route('/api/ml/ventas')
def ml_ventas():
    """Devuelve ventas de los últimos 30 días. Cruza por SKU si está disponible,
    sino agrupa por item_id+titulo para matching por título en el frontend."""
    token = ml_token()
    if not token:
        return jsonify({'error': 'No autorizado. Reconectá ML.', 'auth_url':
            f'https://auth.mercadolibre.com.ar/authorization?response_type=code&client_id={ML_CLIENT_ID}&redirect_uri={ML_REDIRECT_URI}'}), 401

    me = req_lib.get('https://api.mercadolibre.com/users/me',
                     headers={'Authorization': f'Bearer {token}'}).json()
    seller_id = me.get('id')
    if not seller_id:
        return jsonify({'error': 'No se pudo obtener el seller ID'}), 500

    desde = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%dT00:00:00.000-03:00')

    # Acumular ventas por item_id
    ventas_item = {}
    offset = 0
    while True:
        url = (f'https://api.mercadolibre.com/orders/search'
               f'?seller={seller_id}&order.date_created.from={desde}'
               f'&order.status=paid&limit=50&offset={offset}')
        resp = req_lib.get(url, headers={'Authorization': f'Bearer {token}'}).json()
        ordenes = resp.get('results', [])
        if not ordenes:
            break
        for orden in ordenes:
            for item in orden.get('order_items', []):
                item_data = item.get('item', {})
                item_id = item_data.get('id', '')
                sku = (item_data.get('seller_sku') or '').strip()
                titulo = item_data.get('title', '')
                qty = item.get('quantity', 0)
                key = item_id
                if key not in ventas_item:
                    ventas_item[key] = {'item_id': item_id, 'sku': sku, 'titulo': titulo, 'vendidos': 0}
                ventas_item[key]['vendidos'] += qty
                if sku and not ventas_item[key]['sku']:
                    ventas_item[key]['sku'] = sku
        total = resp.get('paging', {}).get('total', 0)
        offset += 50
        if offset >= total:
            break

    # Si algún item no tiene SKU, intentar leerlo desde la API de items
    items_sin_sku = [v['item_id'] for v in ventas_item.values() if not v['sku']]
    for i in range(0, len(items_sin_sku), 20):
        batch = items_sin_sku[i:i+20]
        ids_str = ','.join(batch)
        r = req_lib.get(f'https://api.mercadolibre.com/items?ids={ids_str}&attributes=id,seller_custom_field',
                        headers={'Authorization': f'Bearer {token}'})
        if r.status_code == 200:
            for entry in r.json():
                body = entry.get('body', {})
                iid  = body.get('id', '')
                scf  = (body.get('seller_custom_field') or '').strip()
                if iid in ventas_item and scf:
                    ventas_item[iid]['sku'] = scf

    return jsonify(list(ventas_item.values()))

@app.route('/api/ml/match-debug')
def ml_match_debug():
    """Muestra ventas ML y productos del depósito para diagnosticar el cruce."""
    token = ml_token()
    if not token:
        return jsonify({'error': 'No autorizado'}), 401

    # Productos del depósito
    with engine.connect() as conn:
        prods = [dict(r._mapping) for r in conn.execute(text('SELECT id, nombre, sku, color FROM productos ORDER BY nombre')).fetchall()]

    # Ventas ML (reutiliza la lógica)
    me = req_lib.get('https://api.mercadolibre.com/users/me', headers={'Authorization': f'Bearer {token}'}).json()
    seller_id = me.get('id')
    desde = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%dT00:00:00.000-03:00')
    ventas_item = {}
    offset = 0
    while True:
        url = (f'https://api.mercadolibre.com/orders/search?seller={seller_id}'
               f'&order.date_created.from={desde}&order.status=paid&limit=50&offset={offset}')
        resp = req_lib.get(url, headers={'Authorization': f'Bearer {token}'}).json()
        ordenes = resp.get('results', [])
        if not ordenes: break
        for orden in ordenes:
            for item in orden.get('order_items', []):
                d = item.get('item', {})
                iid = d.get('id','')
                if iid not in ventas_item:
                    ventas_item[iid] = {'item_id':iid,'sku':(d.get('seller_sku') or '').strip(),'titulo':d.get('title',''),'vendidos':0}
                ventas_item[iid]['vendidos'] += item.get('quantity',0)
        total = resp.get('paging',{}).get('total',0)
        offset += 50
        if offset >= total: break

    ventas = list(ventas_item.values())

    # Intentar cruce
    matches = []
    for v in ventas:
        matched_prod = None
        # Por SKU
        if v['sku']:
            for p in prods:
                if (p['sku'] or '').upper() == v['sku'].upper():
                    matched_prod = f"{p['nombre']} {p['color']} (SKU match)"
                    break
        # Por título
        if not matched_prod:
            titulo_up = v['titulo'].upper()
            for p in prods:
                if p['nombre'].upper() in titulo_up:
                    matched_prod = f"{p['nombre']} {p['color']} (título match)"
                    break
        matches.append({'ml_titulo': v['titulo'], 'ml_sku': v['sku'], 'vendidos': v['vendidos'], 'match': matched_prod or '❌ Sin match'})

    return jsonify({'total_ml_items': len(ventas), 'total_productos_deposito': len(prods), 'cruces': matches})

@app.route('/api/ml/status')
def ml_status():
    token = ml_token()
    return jsonify({'conectado': bool(token)})

@app.route('/api/ml/debug')
def ml_debug():
    """Endpoint de diagnóstico: muestra las primeras órdenes y sus items crudos."""
    token = ml_token()
    if not token:
        return jsonify({'error': 'No autorizado'}), 401
    me = req_lib.get('https://api.mercadolibre.com/users/me',
                     headers={'Authorization': f'Bearer {token}'}).json()
    seller_id = me.get('id')
    desde = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%dT00:00:00.000-03:00')
    url = (f'https://api.mercadolibre.com/orders/search'
           f'?seller={seller_id}&order.date_created.from={desde}'
           f'&order.status=paid&limit=3&offset=0')
    resp = req_lib.get(url, headers={'Authorization': f'Bearer {token}'}).json()
    total = resp.get('paging', {}).get('total', 0)
    ordenes = resp.get('results', [])
    muestra = []
    for o in ordenes:
        for it in o.get('order_items', []):
            muestra.append({
                'order_id': o.get('id'),
                'item_id': it.get('item', {}).get('id'),
                'title': it.get('item', {}).get('title'),
                'seller_sku': it.get('item', {}).get('seller_sku'),
                'seller_custom_field': it.get('item', {}).get('seller_custom_field'),
                'quantity': it.get('quantity'),
            })
    # Mostrar detalle del primer item - respuesta completa para diagnóstico
    item_detalle = {}
    if muestra:
        item_id = muestra[0]['item_id']
        r_item = req_lib.get(f'https://api.mercadolibre.com/items/{item_id}?include_attributes=all',
                          headers={'Authorization': f'Bearer {token}'})
        det = r_item.json()
        item_detalle = {
            'status_code': r_item.status_code,
            'id': det.get('id'),
            'title': det.get('title'),
            'seller_custom_field': det.get('seller_custom_field'),
            'error': det.get('error'),
            'message': det.get('message'),
            'attributes_count': len(det.get('attributes', [])),
            'attributes_sample': det.get('attributes', [])[:5],
        }
    return jsonify({'seller_id': seller_id, 'total_ordenes_30d': total, 'muestra': muestra, 'item_detalle': item_detalle})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'\n  Depósito corriendo en: http://localhost:{port}\n')
    app.run(debug=False, host='0.0.0.0', port=port)
