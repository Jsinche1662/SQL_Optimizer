-- DDL de ejemplo. Si introduces un error de sintaxis aquí
-- (por ejemplo: borrar el paréntesis de cierre), el workflow
-- sql-syntax-check.yml fallará y la Branch Protection Rule
-- bloqueará el merge.

CREATE TABLE clientes 
    id          INTEGER       NOT NULL PRIMARY KEY,
    nombre      VARCHAR(120)  NOT NULL,
    pais        CHAR(2)       NOT NULL,
    creado_en   TIMESTAMP     NOT NULL
);

CREATE INDEX idx_clientes_pais ON clientes (pais);
