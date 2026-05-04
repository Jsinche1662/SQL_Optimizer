-- Otro ejemplo: subquery correlacionada candidata a JOIN.
SELECT p.id,
       p.nombre,
       (SELECT COUNT(*)
          FROM ventas v
         WHERE v.producto_id = p.id) AS total_ventas
FROM productos p
WHERE p.activo = 1;
