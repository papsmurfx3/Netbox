#!/usr/bin/env python3
"""
generate_install_pdf.py

Usage:
  export NETBOX_TOKEN="0123456789abcdef..."
  python generate_install_pdf.py --base-url https://netbox.example.com --device FW-ATL-01 --out ./FW-ATL-01-install.pdf

This script:
- Queries NetBox for device, rack, interfaces, and cable info
- Builds a context
- Renders an HTML template via Jinja2
- Converts HTML -> PDF using WeasyPrint (recommended) or pdfkit fallback
"""

import os
import sys
import argparse
import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path

# Optional PDF backends
USE_WEASYPRINT = True
try:
    if USE_WEASYPRINT:
        from weasyprint import HTML
except Exception:
    try:
        import pdfkit
        USE_WEASYPRINT = False
    except Exception:
        USE_WEASYPRINT = None

# Basic cable color normalization (extend as needed)
COLOR_MAP = {
    'blu': 'Blue', 'blue': 'Blue', 'b': 'Blue',
    'red': 'Red', 'r': 'Red',
    'yellow': 'Yellow', 'yel': 'Yellow',
    'orange': 'Orange', 'org': 'Orange',
    'green': 'Green', 'grn': 'Green',
    'black': 'Black', 'blk': 'Black',
    'white': 'White', 'wht': 'White'
}

def normalize_color(text):
    if not text:
        return ''
    t = text.strip().lower()
    return COLOR_MAP.get(t, text)  # fallback to original

class NetBoxAPI:
    def __init__(self, base_url, token, verify_ssl=True):
        base_url = base_url.rstrip('/')
        self.base = base_url + '/api'
        self.s = requests.Session()
        self.s.headers.update({'Authorization': f'Token {token}', 'Accept': 'application/json'})
        self.verify = verify_ssl

    def get(self, path, params=None):
        url = self.base + path
        r = self.s.get(url, params=params, verify=self.verify, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_device(self, device_name):
        data = self.get('/dcim/devices/', params={'name': device_name})
        results = data.get('results', [])
        return results[0] if results else None

    def get_rack(self, rack_id):
        if not rack_id:
            return None
        return self.get(f'/dcim/racks/{rack_id}/')

    def get_device_interfaces(self, device_id):
        data = self.get('/dcim/interfaces/', params={'device_id': device_id, 'limit': 1000})
        return data.get('results', [])

    def get_cable(self, cable_id):
        return self.get(f'/dcim/cables/{cable_id}/') if cable_id else None

    def get_device_by_id(self, device_id):
        return self.get(f'/dcim/devices/{device_id}/')

def build_context(nb: NetBoxAPI, device_name: str):
    device = nb.get_device(device_name)
    if not device:
        raise SystemExit(f"Device '{device_name}' not found in NetBox.")

    device_id = device['id']
    # Rack info
    rack = None
    if device.get('rack'):
        rack = nb.get_rack(device['rack']['id'])

    # Interfaces
    interfaces = nb.get_device_interfaces(device_id)
    connected_ifaces = []
    for iface in interfaces:
        # include only cabled connections (cable is set and connection exists)
        # NetBox represents connection via cable / connected_endpoint
        cable = iface.get('cable')
        if not cable:
            continue

        # get the cable details (sometimes partial in interface object)
        cable_id = cable.get('id') if isinstance(cable, dict) else cable
        cable_obj = None
        try:
            cable_obj = nb.get_cable(cable_id) if cable_id else None
        except Exception:
            cable_obj = None

        # Determine remote endpoint: netbox cable object has termination_a and termination_b,
        # interfaces have 'connected_endpoint' data sometimes. We'll try to infer remote.
        remote_device = ''
        remote_interface = ''
        remote_rack = ''
        if cable_obj:
            # Determine which side is local vs remote
            term_a = cable_obj.get('termination_a')
            term_b = cable_obj.get('termination_b')
            # interface side URL includes interface id; compare device id
            def term_is_local(term):
                if not term:
                    return False
                # term may include 'object_type' and 'device' or 'device' key
                # fallback: compare device id
                td = term.get('device') or (term.get('object') and term.get('object').get('device'))
                try:
                    return td and td['id'] == device_id
                except Exception:
                    return False

            local_term = None
            remote_term = None
            if term_a and term_is_local(term_a):
                local_term = term_a
                remote_term = term_b
            elif term_b and term_is_local(term_b):
                local_term = term_b
                remote_term = term_a
            else:
                # fallback: if interface object has connected_endpoint
                connected = iface.get('connected_endpoint')
                if connected and connected.get('device'):
                    # remote = connected
                    remote_term = connected
            if remote_term:
                # remote device
                rd = remote_term.get('device') or remote_term.get('object') and remote_term.get('object').get('device')
                ri = remote_term.get('name') or (remote_term.get('object') and remote_term.get('object').get('name'))
                if rd:
                    remote_device = rd.get('display', rd.get('name') if 'name' in rd else '')
                    # fetch remote rack if available
                    if rd.get('id'):
                        try:
                            rd_obj = nb.get_device_by_id(rd.get('id'))
                            if rd_obj and rd_obj.get('rack'):
                                remote_rack = rd_obj['rack']['display'] if rd_obj['rack'] else ''
                        except Exception:
                            pass
                if ri:
                    remote_interface = ri

        # cable color from cable_obj or from interface.custom_fields (if used)
        cable_color = ''
        if cable_obj:
            cable_color = cable_obj.get('color') or ''
        # fallback to interface.custom_fields
        if not cable_color:
            cf = iface.get('custom_fields') or {}
            cable_color = cf.get('cable_color', '')

        cable_color = normalize_color(cable_color)

        connected_ifaces.append({
            'name': iface.get('name') or iface.get('label') or iface.get('display'),
            'description': iface.get('description') or '',
            'cable_color': cable_color,
            'remote_device': remote_device,
            'remote_interface': remote_interface,
            'remote_rack': remote_rack
        })

    # Sort connections by local interface name
    connected_ifaces.sort(key=lambda x: x['name'])

    # Rack U position (if available)
    rack_info = {}
    if device.get('position'):
        rack_info['position'] = device.get('position')
        rack_info['face'] = device.get('face') or ''
    if rack:
        rack_info.update({
            'name': rack.get('name'),
            'site': rack.get('site', {}).get('name') if rack.get('site') else '',
            'u_height': rack.get('u_height')
        })

    context = {
        'device': {
            'name': device.get('name'),
            'display': device.get('display_name') or device.get('name'),
            'model': device.get('device_type', {}).get('model') or device.get('device_type', {}).get('display', ''),
            'manufacturer': (device.get('device_type', {}) .get('manufacturer', {}) .get('display', '')) if device.get('device_type') else '',
            'serial': device.get('serial')
        },
        'rack': rack_info,
        'connections': connected_ifaces
    }
    return context

def render_html(template_dir, template_name, context):
    env = Environment(loader=FileSystemLoader(template_dir),
                      autoescape=select_autoescape(['html', 'xml']))
    tmpl = env.get_template(template_name)
    return tmpl.render(context)

def html_to_pdf(html_string, out_path):
    if USE_WEASYPRINT:
        HTML(string=html_string).write_pdf(out_path)
    elif USE_WEASYPRINT is False:
        # pdfkit fallback (wkhtmltopdf required)
        pdfkit.from_string(html_string, out_path)
    else:
        raise RuntimeError("No PDF backend available. Install WeasyPrint or pdfkit + wkhtmltopdf.")

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--base-url', required=True, help='NetBox base URL, e.g. https://netbox.example.com')
    p.add_argument('--device', required=True, help='Device name (exact match) to generate PDF for')
    p.add_argument('--out', required=True, help='Output PDF path')
    p.add_argument('--token', default=os.getenv('NETBOX_TOKEN'), help='NetBox API token (or set NETBOX_TOKEN)')
    p.add_argument('--no-verify-ssl', action='store_true', help='Disable SSL verification (not recommended)')
    return p.parse_args()

def main():
    args = parse_args()
    if not args.token:
        print("NETBOX_TOKEN not set and --token not provided.", file=sys.stderr)
        sys.exit(2)

    nb = NetBoxAPI(args.base_url, args.token, verify_ssl=not args.no_verify_ssl)
    try:
        ctx = build_context(nb, args.device)
    except Exception as e:
        print("ERROR building context:", e, file=sys.stderr)
        sys.exit(3)

    # Path to templates relative to script
    template_dir = Path(__file__).parent / 'templates'
    html = render_html(str(template_dir), 'device_report.html', ctx)

    out_path = Path(args.out).resolve()
    try:
        html_to_pdf(html, str(out_path))
        print(f"PDF written to {out_path}")
    except Exception as e:
        print("ERROR generating PDF:", e, file=sys.stderr)
        # For debugging, write HTML to disk
        debug_html = out_path.with_suffix('.html')
        debug_html.write_text(html, encoding='utf-8')
        print(f"Wrote debug HTML to {debug_html}", file=sys.stderr)
        sys.exit(4)

if __name__ == '__main__':
    main()
