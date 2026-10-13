HEALTH_PATH = "/__cat_browser_bridge/health"
HEALTH_SERVICE = "oussama-cutter"


def install_health_route(app):
    @app.get(HEALTH_PATH, include_in_schema=False)
    def browser_bridge_health():
        return {"service": HEALTH_SERVICE, "ok": True}
