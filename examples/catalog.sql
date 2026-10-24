CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0)
);
INSERT INTO products VALUES
    (1, 'apple', 'fruit', 300),
    (2, 'banana', 'fruit', 200),
    (3, 'notebook', 'stationery', 1200);
