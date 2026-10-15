"""Shipment lifecycle shared by checkout and the administrative panel."""
from datetime import date
from flask import abort
from psycopg.types.json import Jsonb
from db import audit

TRANSITIONS = {
    'PENDIENTE': {'PENDIENTE', 'ENVIADO'},
    'ENVIADO': {'ENVIADO', 'INCIDENCIA', 'ENTREGADO'},
    'INCIDENCIA': {'INCIDENCIA', 'ENVIADO', 'ENTREGADO'},
    'ENTREGADO': {'ENTREGADO'},
}


def shipment_event(conn, row, actor, notes=''):
    conn.execute('''INSERT INTO boutique.shipment_events(order_id,status,carrier,tracking_id,notes,actor)
                    VALUES (%s,%s,%s,%s,%s,%s)''',
                 (row['id'], row['fulfillment'], row['shipping_carrier'],
                  row['payload'].get('shippingTrackingId', ''), notes, actor))


def update_shipment(conn, row, data, actor, request_id):
    if not isinstance(data, dict):
        raise ValueError('Datos del envío inválidos')
    if row['state'] != 'confirmed':
        abort(409, 'Solo se pueden gestionar envíos de pedidos confirmados')
    try:
        version = int(data.get('version', 0))
    except (ValueError, TypeError):
        raise ValueError('Versión del envío inválida')
    if version != row['shipment_version']:
        abort(409, 'El envío cambió; recarga el detalle antes de guardar')
    target = str(data.get('status', row['fulfillment']))
    if target not in TRANSITIONS.get(row['fulfillment'], set()):
        abort(409, 'Transición de envío no permitida')
    carrier = str(data.get('carrier', '')).strip()
    tracking = str(data.get('tracking_id', '')).strip()
    notes = str(data.get('notes', '')).strip()
    if len(carrier) > 128 or len(tracking) > 128 or len(notes) > 2000:
        raise ValueError('Paquetería, guía o notas exceden la longitud permitida')
    if target != 'PENDIENTE' and (not carrier or not tracking):
        raise ValueError('Paquetería y guía son obligatorias para enviar el pedido')
    if target == 'INCIDENCIA' and not notes:
        raise ValueError('Describe la incidencia del envío')
    eta = data.get('estimated_delivery') or None
    if eta:
        try:
            eta = date.fromisoformat(eta)
        except (ValueError, TypeError):
            raise ValueError('Fecha estimada inválida; usa YYYY-MM-DD')
    payload = dict(row['payload'], shippingTrackingId=tracking)
    updated = conn.execute('''UPDATE boutique.orders SET fulfillment=%s,shipping_carrier=%s,
             shipping_notes=%s,estimated_delivery=%s,payload=%s,shipment_version=shipment_version+1,
             updated_at=now() WHERE id=%s RETURNING *''',
             (target, carrier, notes, eta, Jsonb(payload), row['id'])).fetchone()
    shipment_event(conn, updated, actor, notes)
    audit(conn, actor, 'shipment.' + target.lower(), str(row['id']), request_id)
    return updated
