# app/utils/response.py

def success_response(message: str, data=None):
    return {
        "status": True,
        "message": message,
        "data": data
    }


def error_response(message: str):
    return {
        "status": False,
        "message": message,
        "data": None
    }