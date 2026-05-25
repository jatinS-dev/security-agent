from __future__ import annotations


def openapi_spec() -> dict:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Sentient API",
            "version": "0.1.0",
        },
        "paths": {
            "/v1/health": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/v1/ready": {"get": {"responses": {"200": {"description": "Ready"}}}},
            "/metrics": {
                "get": {
                    "summary": "Prometheus-style service metrics",
                    "responses": {"200": {"description": "Metrics"}},
                }
            },
            "/v1/decide": {
                "post": {
                    "summary": "Evaluate an agent event",
                    "parameters": [
                        {
                            "name": "X-Tenant-ID",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Decision"}},
                }
            },
            "/v1/approvals": {
                "get": {
                    "summary": "List pending approvals",
                    "parameters": [
                        {
                            "name": "X-Tenant-ID",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Approvals"}},
                }
            },
            "/v1/approvals/{request_id}/approve": {
                "post": {
                    "summary": "Approve a request",
                    "responses": {"200": {"description": "Approval"}},
                }
            },
            "/v1/approvals/{request_id}/reject": {
                "post": {
                    "summary": "Reject a request",
                    "responses": {"200": {"description": "Approval"}},
                }
            },
            "/v1/audit": {
                "get": {
                    "summary": "List audit records",
                    "responses": {"200": {"description": "Audit records"}},
                }
            },
        },
    }
