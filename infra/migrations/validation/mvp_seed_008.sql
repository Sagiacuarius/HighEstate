\set ON_ERROR_STOP on
-- Agent Autonomy seed (change: agent-autonomy, WU15). Requires migration 008.
-- Extends the accepted mvp_seed.sql baseline (+ mvp_seed_005.sql catalog rows):
-- same fail-closed ownership rules (designated ids + demo_* markers, advisory
-- lock, single transaction). It only enriches rows it designates and never
-- claims foreign/unmarked rows. Re-apply converges (DELETE-then-INSERT).
-- Content: 16 curated KB articles per demo account (2 per category across the
-- 8 brief categories; markers demo-a-kb-001..016 / demo-b-kb-001..016), the
-- 'Calificado' qualification-stage row per demo pipeline (the B3 scenario
-- target; mvp_seed.sql carries only 'Nueva consulta' (A) / 'Visita agendada'
-- (B)), and the agent_mode='auto' flip on the two marker-owned demo
-- conversations. Requires the 008 columns; fails closed without them.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('mvp-commercial-demo-seed-reset'));

-- Ownership gate for every row this seed designates. Any row at a designated
-- id (or holding a designated marker) must already belong to the expected
-- account and carry the exact marker; otherwise abort before touching anything.
DO $$
DECLARE
  expected record;
  found_account uuid;
  found_key text;
  marker_owner uuid;
BEGIN
  FOR expected IN
    SELECT * FROM (VALUES
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000001'::uuid, '20000000-0000-0000-0000-000000000001'::uuid, 'demo-a-kb-001'::text),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-002'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-003'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-004'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000005', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-005'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000006', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-006'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000007', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-007'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000008', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-008'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000009', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-009'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000010', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-010'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000011', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-011'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000012', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-012'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000013', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-013'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000014', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-014'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000015', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-015'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000016', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-016'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000017', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-001'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000018', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-002'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000019', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-003'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000020', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-004'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000021', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-005'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000022', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-006'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000023', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-007'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000024', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-008'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000025', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-009'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000026', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-010'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000027', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-011'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000028', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-012'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000029', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-013'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000030', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-014'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000031', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-015'),
      ('knowledge_base_articles', '6f000000-0000-0000-0000-000000000032', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-016'),
      ('pipeline_stages',         '64000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001', 'demo-a-stage-calificado'),
      ('pipeline_stages',         '64000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000002', 'demo-b-stage-calificado'),
      ('conversations',           '66000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-conversation-001'),
      ('conversations',           '66000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-conversation-001')
    ) AS v(tbl, id, account, key)
  LOOP
    EXECUTE format('SELECT client_account_id, demo_seed_key FROM %I WHERE id = $1', expected.tbl)
      INTO found_account, found_key USING expected.id;
    IF found_account IS NOT NULL
       AND (found_account <> expected.account OR found_key IS DISTINCT FROM expected.key) THEN
      RAISE EXCEPTION 'seed row % in % conflicts with an existing row at id %; refusing to claim it',
        expected.key, expected.tbl, expected.id;
    END IF;
    EXECUTE format('SELECT id FROM %I WHERE demo_seed_key = $1 AND (id <> $2 OR client_account_id <> $3) LIMIT 1', expected.tbl)
      INTO marker_owner USING expected.key, expected.id, expected.account;
    IF marker_owner IS NOT NULL THEN
      RAISE EXCEPTION 'marker % in % is already held by row %; refusing to seed',
        expected.key, expected.tbl, marker_owner;
    END IF;
  END LOOP;
END $$;

-- Converge: remove only rows this seed designates (marker + account). The KB
-- table has no children; the new stages carry no leads. Designated demo
-- conversations are never deleted here (only their agent_mode flips below).
DELETE FROM knowledge_base_articles WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-001'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-002'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-003'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-004'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-005'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-006'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-007'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-008'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-009'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-010'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-011'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-012'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-013'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-014'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-015'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-kb-016'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-001'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-002'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-003'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-004'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-005'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-006'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-007'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-008'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-009'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-010'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-011'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-012'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-013'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-014'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-015'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-kb-016'));
DELETE FROM pipeline_stages WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-stage-calificado'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-stage-calificado'));

-- Qualification-stage rows (B3 transition_lead target; one per demo pipeline).
INSERT INTO pipeline_stages (id, client_account_id, pipeline_id, demo_seed_key, name, position) VALUES
  ('64000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001', '63000000-0000-0000-0000-000000000001', 'demo-a-stage-calificado', 'Calificado', 1),
  ('64000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000002', '63000000-0000-0000-0000-000000000002', 'demo-b-stage-calificado', 'Calificado', 1);

-- Knowledge base: account A (Aurea Inmobiliaria Norte, Buenos Aires).
INSERT INTO knowledge_base_articles (id, client_account_id, demo_seed_key, category, question, keywords, answer) VALUES
  ('6f000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-001', 'expensas',
   '¿Qué incluyen las expensas del departamento de Palermo?',
   '{expensas,edificio,gastos,mantenimiento,encargado,departamento}',
   'Las expensas del edificio de Thames 1500 rondan los $45.000 mensuales e incluyen encargados, limpieza de partes comunes, mantenimiento de ascensores y el fondo de reserva. El detalle exacto figura en la última liquidación del consorcio.'),
  ('6f000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-002', 'expensas',
   '¿Las expensas incluyen el ABL?',
   '{expensas,abl,impuesto,municipal,diferencia,pago}',
   'No, las expensas y el ABL son conceptos distintos: las expensas cubren los gastos comunes del edificio y el ABL es el impuesto municipal que se paga aparte todos los meses.'),
  ('6f000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-003', 'escritura',
   '¿Cuánto tarda la escritura de una propiedad?',
   '{escritura,escribano,registro,plazos,propiedad,firma}',
   'Desde la reserva hasta la firma suelen pasar entre 30 y 45 días, según los tiempos del escribano y del Registro de la Propiedad. Te acompañamos en cada paso para que no se demore.'),
  ('6f000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-004', 'escritura',
   '¿Qué gastos tiene la escritura?',
   '{escritura,gastos,honorarios,sellos,impuestos,escribano}',
   'Además del precio, calculá entre un 6 y un 8 por ciento por honorarios del escribano, sellos e impuestos. Te pasamos el estimado exacto antes de reservar.'),
  ('6f000000-0000-0000-0000-000000000005', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-005', 'garantia',
   '¿Aceptan garantía de fiador para alquilar?',
   '{garantia,fiador,garante,alquiler,requisito,contrato}',
   'Sí, aceptamos fiador con recibo de sueldo demostrable o título de propiedad en la ciudad. El fiador firma junto al contrato y responde ante cualquier incumplimiento.'),
  ('6f000000-0000-0000-0000-000000000006', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-006', 'garantia',
   '¿Puedo alquilar con seguro de caución en lugar de fiador?',
   '{garantia,seguro,caucion,alquiler,fiador,inquilino}',
   'Sí, trabajamos con las principales compañías de seguro de caución. El costo lo paga el inquilino y reemplaza por completo la garantía tradicional.'),
  ('6f000000-0000-0000-0000-000000000007', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-007', 'documentos',
   '¿Qué documentos necesito para alquilar?',
   '{documentos,alquilar,dni,recibo,sueldo,garante}',
   'DNI vigente, últimos tres recibos de sueldo o comprobante de ingresos, y la documentación del garante. Con eso armamos la carpeta en el día.'),
  ('6f000000-0000-0000-0000-000000000008', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-008', 'documentos',
   '¿Piden recibo de sueldo para comprar una propiedad?',
   '{documentos,comprar,recibo,ingresos,credito,banco}',
   'Solo si vas a tomar un crédito hipotecario; para una compra al contado pedimos DNI y los datos para la reserva. El banco define sus propios requisitos de ingresos.'),
  ('6f000000-0000-0000-0000-000000000009', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-009', 'financiacion',
   '¿El PH de Belgrano se puede pagar en cuotas?',
   '{financiacion,cuotas,ph,pago,precio,dolares}',
   'El PH de Superí 1200 está publicado en USD 210.000 y el vendedor evalúa una entrega inicial más cuotas en dólares. Contanos tu propuesta y la gestionamos.'),
  ('6f000000-0000-0000-0000-000000000010', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-010', 'financiacion',
   '¿Aceptan permuta como parte de pago?',
   '{financiacion,permuta,canje,propiedad,pago,tasacion}',
   'Sí, tomamos permutas de propiedades en Buenos Aires como parte de pago. Tasamos tu propiedad sin cargo y compensamos la diferencia.'),
  ('6f000000-0000-0000-0000-000000000011', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-011', 'amenities',
   '¿El edificio de Palermo tiene cochera?',
   '{amenities,cochera,estacionamiento,edificio,balcon,baulera}',
   'El loft de Thames 1500 incluye una cochera fija en el subsuelo más baulera. El PH de Belgrano no tiene cochera propia, pero hay una playa a media cuadra.'),
  ('6f000000-0000-0000-0000-000000000012', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-012', 'amenities',
   '¿Aceptan mascotas en las propiedades?',
   '{amenities,mascotas,patio,perros,balcon,edificio}',
   'Sí, tanto el loft de Palermo como el PH de Belgrano aceptan mascotas. El PH suma un patio de 23 metros ideal para perros.'),
  ('6f000000-0000-0000-0000-000000000013', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-013', 'requisitos',
   '¿Cuáles son los requisitos para ingresar como inquilino?',
   '{requisitos,ingreso,inquilino,alquiler,documentos,garantia}',
   'DNI, comprobantes de ingresos de los últimos tres meses y garantía de fiador o seguro de caución. La carpeta se aprueba en 48 horas hábiles.'),
  ('6f000000-0000-0000-0000-000000000014', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-014', 'requisitos',
   '¿Piden ingresos mínimos para alquilar?',
   '{requisitos,ingresos,minimos,sueldo,alquiler,mensual}',
   'Pedimos ingresos demostrables equivalentes a tres veces el valor del alquiler mensual, sumando todos los integrantes del grupo familiar si hace falta.'),
  ('6f000000-0000-0000-0000-000000000015', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-015', 'visitas',
   '¿Cómo coordino una visita a una propiedad?',
   '{visitas,coordinar,visita,horarios,asesor,propiedad}',
   'Escribinos por acá con el día y horario que prefieras y te confirmamos con el asesor a cargo. Las visitas duran unos 30 minutos.'),
  ('6f000000-0000-0000-0000-000000000016', '20000000-0000-0000-0000-000000000001', 'demo-a-kb-016', 'visitas',
   '¿Hacen visitas los fines de semana?',
   '{visitas,fin,semana,sabado,horarios,propietario}',
   'Sí, coordinamos visitas los sábados de 10 a 13. Los domingos solo con reserva previa y según disponibilidad del propietario.');

-- Knowledge base: account B (Litoral Inmobiliaria Sur, Rosario).
INSERT INTO knowledge_base_articles (id, client_account_id, demo_seed_key, category, question, keywords, answer) VALUES
  ('6f000000-0000-0000-0000-000000000017', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-001', 'expensas',
   '¿La casa de Fisherton paga expensas?',
   '{expensas,casa,impuestos,servicios,fisherton,vivienda}',
   'Al ser una casa particular no paga expensas: solo los impuestos y servicios de la vivienda. Es una ventaja frente a los departamentos del centro.'),
  ('6f000000-0000-0000-0000-000000000018', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-002', 'expensas',
   '¿Cuánto se paga de ABL en Rosario?',
   '{expensas,abl,impuesto,municipal,rosario,tasa}',
   'La tasa municipal de Rosario depende de la zona y la valuación; para la casa de Fisherton ronda un monto similar al de un departamento céntrico. Te pasamos la última boleta en la visita.'),
  ('6f000000-0000-0000-0000-000000000019', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-003', 'escritura',
   '¿La casa de Fisherton tiene escritura?',
   '{escritura,casa,fisherton,titulo,plano,escribano}',
   'Sí, la casa cuenta con escritura traslativa y plano aprobado. El título está limpio y listo para escriturar en 30 días.'),
  ('6f000000-0000-0000-0000-000000000020', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-004', 'escritura',
   '¿Quién elige al escribano en la compra?',
   '{escritura,escribano,comprador,gastos,honorarios,compra}',
   'Por costumbre en Rosario lo elige el comprador, y los honorarios se reparten según la ley. Te recomendamos escribanos de confianza si no tenés uno.'),
  ('6f000000-0000-0000-0000-000000000021', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-005', 'garantia',
   '¿Qué garantía piden para alquilar en Rosario?',
   '{garantia,fiador,alquiler,rosario,seguro,requisito}',
   'Pedimos fiador con recibo de sueldo o propiedad en la provincia, o seguro de caución vigente. Las condiciones son claras desde la primera visita.'),
  ('6f000000-0000-0000-0000-000000000022', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-006', 'garantia',
   '¿El seguro de caución cubre todo el contrato?',
   '{garantia,seguro,caucion,contrato,alquiler,poliza}',
   'Sí, cubre alquileres impagos, expensas y daños durante todo el contrato. La póliza se renueva cada año junto con el ajuste.'),
  ('6f000000-0000-0000-0000-000000000023', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-007', 'documentos',
   '¿Qué papeles necesito para alquilar el departamento del centro?',
   '{documentos,alquilar,dni,recibo,monotributo,garantia}',
   'DNI, dos últimos recibos de sueldo y garantía. Si sos independiente, los últimos tres pagos del monotributo sirven igual.'),
  ('6f000000-0000-0000-0000-000000000024', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-008', 'documentos',
   '¿Necesito precalificación bancaria para ofertar?',
   '{documentos,credito,banco,oferta,reserva,hipotecario}',
   'No para ofertar, pero si vas a usar crédito hipotecario te conviene llegar con la precalificación aprobada para reservar más rápido.'),
  ('6f000000-0000-0000-0000-000000000025', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-009', 'financiacion',
   '¿El departamento del centro acepta financiación?',
   '{financiacion,cuotas,pesos,departamento,centro,precio}',
   'Esa unidad ya se vendió, pero las nuevas que ingresan suelen aceptar entrega más cuotas en pesos ajustadas por CAC. Avisanos tu presupuesto y te alertamos.'),
  ('6f000000-0000-0000-0000-000000000026', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-010', 'financiacion',
   '¿Toman auto como parte de pago?',
   '{financiacion,pago,auto,tasacion,efectivo,operacion}',
   'Evaluamos vehículos como parte de pago en operaciones puntuales, con tasación a valor de mercado. El saldo se integra en efectivo o transferencia.'),
  ('6f000000-0000-0000-0000-000000000027', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-011', 'amenities',
   '¿La casa de Fisherton tiene pileta?',
   '{amenities,pileta,cochera,quincho,casa,terreno}',
   'No tiene pileta propia, pero el terreno de 340 metros permite construirla. La cochera doble techada y el quincho ya están incluidos.'),
  ('6f000000-0000-0000-0000-000000000028', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-012', 'amenities',
   '¿El barrio tiene seguridad?',
   '{amenities,seguridad,barrio,alarma,fisherton,casa}',
   'Fisherton es un barrio residencial tranquilo con vigilancia privada en varias cuadras. La casa además tiene alarma y rejas perimetrales.'),
  ('6f000000-0000-0000-0000-000000000029', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-013', 'requisitos',
   '¿Qué requisitos piden para alquilar un local?',
   '{requisitos,alquiler,documentos,cuit,vivienda,ingreso}',
   'Este catálogo es de viviendas; para locales pedimos los mismos documentos más la constancia de CUIT y el destino comercial declarado.'),
  ('6f000000-0000-0000-0000-000000000030', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-014', 'requisitos',
   '¿Puedo alquilar si soy del interior?',
   '{requisitos,alquiler,ingresos,sueldo,garante,inquilino}',
   'Sí, muchos inquilinos llegan de otras ciudades. Aceptamos recibos de sueldo de cualquier provincia y garante con propiedad en el país.'),
  ('6f000000-0000-0000-0000-000000000031', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-015', 'visitas',
   '¿Puedo visitar la casa un día de semana a la tarde?',
   '{visitas,visita,coordinar,horarios,casa,semana}',
   'Sí, coordinamos visitas de lunes a viernes de 14 a 19. La casa está ocupada por sus dueños, así que pedimos confirmar con un día de anticipación.'),
  ('6f000000-0000-0000-0000-000000000032', '20000000-0000-0000-0000-000000000002', 'demo-b-kb-016', 'visitas',
   '¿Hacen videollamadas para ver propiedades a distancia?',
   '{visitas,videollamada,distancia,whatsapp,visita,propiedad}',
   'Sí, hacemos videollamada por WhatsApp para quienes están lejos y después coordinamos la visita presencial. Dura unos 20 minutos por propiedad.');

-- OD-3: flip the two marker-owned demo conversations to auto. Marker-gated:
-- the gate above already failed closed on foreign/unmarked rows, so this
-- UPDATE touches exactly the two designated conversations.
UPDATE conversations SET agent_mode = 'auto', updated_at = now()
WHERE id IN ('66000000-0000-0000-0000-000000000001',
             '66000000-0000-0000-0000-000000000002');

COMMIT;
