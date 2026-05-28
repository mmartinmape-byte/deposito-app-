from flask import Flask, render_template, request, jsonify
from sqlalchemy import create_engine, text, event
from sqlalchemy.exc import IntegrityError
from datetime import datetime
import os

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


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


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
        ]

    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))


init_db()


# ── Rutas ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


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
                ok = _sub(conn, d['palet_id'], d['producto'], d['color'], d['cajas'])
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
                ok  = _sub(conn, d['palet_id'], d['producto'], d['color'], d['cajas'])
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
    row = conn.execute(
        text('SELECT id FROM stock WHERE palet_id=:pid AND producto=:prod AND color=:col'),
        {'pid': palet_id, 'prod': producto, 'col': color}
    ).fetchone()
    if row:
        conn.execute(
            text('UPDATE stock SET cajas=cajas+:c, piezas_por_caja=:ppk WHERE id=:id'),
            {'c': cajas, 'ppk': ppk, 'id': row.id}
        )
    else:
        conn.execute(
            text('INSERT INTO stock (palet_id,producto,color,cajas,piezas_por_caja) VALUES (:pid,:prod,:col,:c,:ppk)'),
            {'pid': palet_id, 'prod': producto, 'col': color, 'c': cajas, 'ppk': ppk}
        )


def _sub(conn, palet_id, producto, color, cajas):
    row = conn.execute(
        text('SELECT id, cajas FROM stock WHERE palet_id=:pid AND producto=:prod AND color=:col'),
        {'pid': palet_id, 'prod': producto, 'col': color}
    ).fetchone()
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
        text('SELECT id FROM stock WHERE palet_id=:pid AND producto=:prod AND color=:col'),
        {'pid': palet_id, 'prod': producto, 'col': color}
    ).fetchone()
    if cajas == 0:
        if row:
            conn.execute(text('DELETE FROM stock WHERE id=:id'), {'id': row.id})
    elif row:
        conn.execute(
            text('UPDATE stock SET cajas=:c, piezas_por_caja=:ppk WHERE id=:id'),
            {'c': cajas, 'ppk': ppk, 'id': row.id}
        )
    else:
        conn.execute(
            text('INSERT INTO stock (palet_id,producto,color,cajas,piezas_por_caja) VALUES (:pid,:prod,:col,:c,:ppk)'),
            {'pid': palet_id, 'prod': producto, 'col': color, 'c': cajas, 'ppk': ppk}
        )


@app.route('/api/reset', methods=['DELETE'])
def reset_todo():
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM movimientos'))
        conn.execute(text('DELETE FROM stock'))
        conn.execute(text('DELETE FROM palets'))
    return jsonify({'ok': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'\n  Depósito corriendo en: http://localhost:{port}\n')
    app.run(debug=False, host='0.0.0.0', port=port)
