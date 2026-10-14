#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2024 Acme Corp. All rights reserved.
# Author: John Doe <john.doe@corp.acme.com>
# Maintainer: 王小明 (wang.xiaoming@corp.acme.com), see PAYGW-101
"""PAYGW payment gateway API.

Originally written by John Doe (@jdoe) for the Acme PAYGW team.
"""
from fastapi import FastAPI

from app.config import settings
from app.routers import payments

app = FastAPI(
    title="Acme PAYGW API",
    description="Payment gateway for Acme. Contact jane.smith@corp.acme.com for access.",
    version="1.4.2",
    contact={
        "name": "Jane Smith",
        "email": "12345678+jsmith-acme@users.noreply.github.com",
        "url": "https://github.com/jsmith-acme",
    },
    servers=[{"url": "https://paygw-api.corp.acme.com", "description": "internal"}],
)

app.include_router(payments.router)


@app.get("/health")
def health() -> dict:
    # TODO(jdoe): add DB ping — ask Jane Smith
    return {"status": "ok", "service": settings.service_name}
