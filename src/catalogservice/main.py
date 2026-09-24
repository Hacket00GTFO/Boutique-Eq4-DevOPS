import os

import boto3
from flask import Flask, jsonify

app = Flask(__name__)
dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


@app.get("/healthz")
def healthz():
    return "ok", 200


@app.get("/catalogo")
def catalogo():
    table = dynamodb.Table(os.environ["TABLE_NAME"])
    response = table.scan(Select="COUNT")
    return jsonify(
        {
            "mensaje": "Hola desde el backend de catalogo",
            "productos_en_tabla": response["Count"],
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
