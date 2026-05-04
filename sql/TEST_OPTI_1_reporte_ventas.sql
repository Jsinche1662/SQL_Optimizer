-- Ejemplo de query candidata a optimización.
-- La PR a develop disparará el workflow sql-optimizer.yml
-- y deberías ver un comentario con el SQL reescrito.

SELECT *
FROM ventas v
WHERE v.fecha >= '2025-01-01'
  AND v.cliente_id IN (
    SELECT c.id
    FROM clientes c
    WHERE c.pais = 'PE'
  )
ORDER BY v.fecha DESC;
