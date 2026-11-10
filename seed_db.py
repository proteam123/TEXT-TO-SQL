"""Create the two demo SQLite databases.

    python seed_db.py

business.db   - clean schema (customers, products, orders, order_items)
enterprise.db - cryptic abbreviated names (cust_nm, ttl_amt ...) + filler tables,
                to show why schema filtering and alias translation matter.
"""

import random
import sqlite3
from datetime import date, timedelta

import config

FIRST = ["Aisha", "Rahul", "Maria", "Chen", "Omar", "Sara", "John", "Priya", "Liam", "Fatima",
         "Noah", "Anika", "Ethan", "Zara", "Lucas", "Mei", "Arjun", "Hana", "Diego", "Nora"]
LAST = ["Khan", "Nair", "Garcia", "Wang", "Ali", "Thomas", "Smith", "Menon", "Brown", "Rahman",
        "Lee", "Pillai", "Martin", "Hassan", "Silva", "Tan"]
COUNTRIES = ["UAE", "India", "USA", "UK", "Germany", "Singapore"]
CATALOG = {
    "Electronics": [("Wireless Earbuds", 59.0), ("Smart Watch", 149.0), ("Bluetooth Speaker", 45.0),
                    ("USB-C Hub", 29.0), ("Webcam HD", 39.0), ("Power Bank", 25.0)],
    "Home": [("Desk Lamp", 22.0), ("Coffee Maker", 79.0), ("Air Purifier", 119.0),
             ("Cushion Set", 18.0), ("Wall Clock", 15.0), ("Storage Box", 12.0)],
    "Books": [("Python Handbook", 35.0), ("Data Science 101", 42.0), ("SQL in Practice", 30.0),
              ("AI Ethics", 27.0), ("Graph Databases", 48.0), ("Statistics Primer", 33.0)],
    "Sports": [("Yoga Mat", 20.0), ("Running Shoes", 89.0), ("Dumbbell Set", 65.0),
               ("Water Bottle", 10.0), ("Cycling Gloves", 17.0), ("Jump Rope", 9.0)],
}
STATUSES = ["completed"] * 15 + ["cancelled"] * 3 + ["pending"] * 2  # 75 / 15 / 10 %


def _rand_date(rng, start, end):
    return start + timedelta(days=rng.randint(0, (end - start).days))


def seed_business(path):
    rng = random.Random(42)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT,
            country TEXT, signup_date TEXT);
        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT, price REAL);
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER REFERENCES customers(customer_id),
            order_date TEXT, status TEXT);
        CREATE TABLE order_items (
            item_id INTEGER PRIMARY KEY,
            order_id INTEGER REFERENCES orders(order_id),
            product_id INTEGER REFERENCES products(product_id),
            quantity INTEGER, unit_price REAL);
        """
    )
    names = [f"{f} {l}" for f in FIRST for l in LAST]
    rng.shuffle(names)
    for i in range(1, 41):
        name = names[i]
        email = name.lower().replace(" ", ".") + "@example.com"
        cur.execute("INSERT INTO customers VALUES (?,?,?,?,?)",
                    (i, name, email, rng.choice(COUNTRIES),
                     _rand_date(rng, date(2024, 1, 1), date(2025, 6, 30)).isoformat()))
    pid = 1
    products = []
    for category, items in CATALOG.items():
        for pname, price in items:
            cur.execute("INSERT INTO products VALUES (?,?,?,?)", (pid, pname, category, price))
            products.append((pid, price))
            pid += 1
    item_id = 1
    for oid in range(1, 401):
        cust = rng.randint(1, 34)  # customers 35-40 never order (handy for "never ordered" questions)
        odate = _rand_date(rng, date(2025, 1, 1), date(2026, 10, 31)).isoformat()
        cur.execute("INSERT INTO orders VALUES (?,?,?,?)", (oid, cust, odate, rng.choice(STATUSES)))
        for _ in range(rng.randint(1, 4)):
            p, price = rng.choice(products)
            cur.execute("INSERT INTO order_items VALUES (?,?,?,?,?)",
                        (item_id, oid, p, rng.randint(1, 5), price))
            item_id += 1
    con.commit()
    con.close()


FILLER = {
    "hr_emp_mst": "emp_id,emp_nm,dpt_cd,hire_dt",
    "hr_dpt_mst": "dpt_id,dpt_nm,mgr_id",
    "fin_gl_ent": "gl_id,acct_cd,dbt_amt,cr_amt,pst_dt",
    "fin_acct_mst": "acct_id,acct_nm,acct_typ",
    "inv_whs_mst": "whs_id,whs_nm,loc_cd",
    "inv_stk_lvl": "stk_id,prd_id,whs_id,qty_oh",
    "sup_vnd_mst": "vnd_id,vnd_nm,ctry_cd",
    "sup_po_hdr": "po_id,vnd_id,po_dt,ttl_amt",
    "sup_po_ln": "po_ln_id,po_id,prd_id,qty,unit_prc",
    "log_aud_trl": "aud_id,usr_nm,actn_cd,crt_dt",
    "sys_cfg_prm": "prm_id,prm_nm,prm_val",
    "mkt_cmp_mst": "cmp_id,cmp_nm,strt_dt,bdgt_amt",
}


def _col_type(col):
    if col.endswith("_id"):
        return "INTEGER"
    if col.endswith(("_amt", "_prc", "qty", "_oh", "bdgt_amt")):
        return "REAL"
    return "TEXT"


def seed_enterprise(path):
    rng = random.Random(7)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE cust_mst (
            cust_id INTEGER PRIMARY KEY, cust_nm TEXT, ctry_cd TEXT, crt_dt TEXT);
        CREATE TABLE prd_mst (
            prd_id INTEGER PRIMARY KEY, prd_nm TEXT, cat_nm TEXT, unit_prc REAL);
        CREATE TABLE ord_hdr (
            ord_id INTEGER PRIMARY KEY,
            cust_id INTEGER REFERENCES cust_mst(cust_id),
            ord_dt TEXT, ttl_amt REAL, sts_cd TEXT);
        CREATE TABLE ord_ln (
            ln_id INTEGER PRIMARY KEY,
            ord_id INTEGER REFERENCES ord_hdr(ord_id),
            prd_id INTEGER REFERENCES prd_mst(prd_id),
            qty INTEGER, unit_prc REAL, ln_amt REAL);
        """
    )
    for i in range(1, 31):
        cur.execute("INSERT INTO cust_mst VALUES (?,?,?,?)",
                    (i, f"{rng.choice(FIRST)} {rng.choice(LAST)}", rng.choice(COUNTRIES),
                     _rand_date(rng, date(2023, 1, 1), date(2025, 1, 1)).isoformat()))
    products = []
    pid = 1
    for category, items in CATALOG.items():
        for pname, price in items:
            cur.execute("INSERT INTO prd_mst VALUES (?,?,?,?)", (pid, pname, category, price))
            products.append((pid, price))
            pid += 1
    ln = 1
    for oid in range(1, 201):
        total = 0.0
        lines = []
        for _ in range(rng.randint(1, 3)):
            p, price = rng.choice(products)
            q = rng.randint(1, 4)
            lines.append((p, q, price, q * price))
            total += q * price
        cur.execute("INSERT INTO ord_hdr VALUES (?,?,?,?,?)",
                    (oid, rng.randint(1, 30),
                     _rand_date(rng, date(2025, 1, 1), date(2026, 10, 31)).isoformat(),
                     round(total, 2), rng.choice(STATUSES).upper()))
        for p, q, price, amt in lines:
            cur.execute("INSERT INTO ord_ln VALUES (?,?,?,?,?,?)", (ln, oid, p, q, price, amt))
            ln += 1
    for table, cols in FILLER.items():
        col_list = cols.split(",")
        defs = ", ".join(f"{c} {_col_type(c)}" for c in col_list)
        cur.execute(f"CREATE TABLE {table} ({defs})")
        for r in range(1, 4):
            vals = [r if _col_type(c) == "INTEGER" else
                    (round(rng.random() * 1000, 2) if _col_type(c) == "REAL" else f"{c}_{r}")
                    for c in col_list]
            cur.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(vals))})", vals)
    con.commit()
    con.close()


def main():
    seed_business(config.DATABASES["business"])
    seed_enterprise(config.DATABASES["enterprise"])
    print(f"Created {config.DATABASES['business']}")
    print(f"Created {config.DATABASES['enterprise']}")


if __name__ == "__main__":
    main()
