import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()


def verify_admin(
    credentials: HTTPBasicCredentials = Depends(security)
):
    username = os.getenv("ADMIN_USERNAME", "")
    password = os.getenv("ADMIN_PASSWORD", "")

    username_valid = secrets.compare_digest(
        credentials.username,
        username
    )

    password_valid = secrets.compare_digest(
        credentials.password,
        password
    )

    if not username_valid or not password_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )

    return True