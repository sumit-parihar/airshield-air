import http.server
import socketserver
import webbrowser
import threading
import sys

PORT = 8080
DIRECTORY = "."

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

def open_browser():
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception as e:
        print(f"[-] Could not open web browser automatically: {e}")

def run_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"[+] AirShield Server launched successfully.")
        print(f"[+] Dashboard address: http://localhost:{PORT}")
        print(f"[+] Press Ctrl+C to terminate the server safely.\n")
        
        # Open default web browser in a separate thread
        threading.Timer(1.0, open_browser).start()
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[-] Shutting down AirShield Server...")
            sys.exit(0)

if __name__ == "__main__":
    print("="*60)
    print("   AirShield Premium Dashboard Local Runner")
    print("="*60)
    print(f"[*] Starting local server on port {PORT}...")
    run_server()
