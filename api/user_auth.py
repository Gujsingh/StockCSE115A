import psycopg2
import argparse
import os
import sys
import re
import json

from datetime import timedelta, datetime, timezone
from dotenv import load_dotenv, find_dotenv
from pathlib import Path
from psycopg2.extras import RealDictCursor
from passlib.hash import bcrypt
from flask import (
    Flask,
    Blueprint,
    flash,
    redirect,
    url_for,
    render_template,
    current_app,
    request,
    jsonify,
)
from flask_jwt_extended import (
    create_access_token,
    get_jwt,
    get_jwt_identity,
    unset_jwt_cookies,
    jwt_required,
    verify_jwt_in_request,
    JWTManager,
)

# load db connection config
load_dotenv(find_dotenv())

HOST = os.getenv("HOST")
PORT = os.getenv("PORT")
DB_NAME = os.getenv("DB_NAME")
USR = os.getenv("USR")
PASSWORD = os.getenv("PASSWORD")

# connect to user DB
conn = psycopg2.connect(
    dbname=DB_NAME, user=USR, password=PASSWORD, host=HOST, port=PORT
)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def IsValidPassword(str):
    regex = "^(?=.*[a-z])(?=." + "*[A-Z])(?=.*\\d)" + "(?=.*[-+_!@#$%^&*., ?]).+$"
    # Compile the ReGex
    p = re.compile(regex)

    # If the string is empty
    # return false
    if len(str) < 8:
        return False

    # return true if string matches ReGex
    if re.search(p, str):
        return True
    else:
        return False


# Adds user to DB
# INPUT: first name, last name, email, username, password
@auth_bp.route("/signup", methods=["GET", "POST"])
def AddUser():
    cur = conn.cursor()

    if request.method == "POST":
        data = json.loads(request.data)
        f_name = data["first_name"]
        l_name = data["last_name"]
        email = data["email"]
        username = data["username"]
        p_word = data["password"]
        verify_p_word = data["verify_password"]

        error = None

        if not f_name or len(f_name) == 0:
            error = "First name is required."
        elif not l_name or len(l_name) == 0:
            error = "Last name is required."
        elif not email or len(email) == 0:
            error = "Email is required."
        elif not username or len(username) == 0:
            error = "Username is required."
        elif not p_word or len(p_word) == 0:
            error = "Password is required."
        elif not verify_p_word or len(verify_p_word) == 0:
            error = "Re-Entered password is required."

        cur.execute("SELECT * from users where email = %(email)s", {"email": email})
        if cur.fetchone() != None:
            error = "User is already registered."

        if IsValidPassword(p_word) == False:
            error = "Password not strong enough."
        elif p_word != verify_p_word:
            error = "Passwords do not match"

        if error == None:
            cur.execute(
                "INSERT INTO users VALUES (%s, %s, %s, %s, %s, %s)",
                (f_name, l_name, email, username, bcrypt.hash(p_word), "{}"),
            )
            conn.commit()
            current_app.logger.info("User %s has been created succesfully.", username)
            return jsonify({"err_msg": "Account creation successful"}), 201

        current_app.logger.error(error)
        return jsonify({"err_msg": error})


# method for refreshing tokens reaching expiration
# Taken from flask-jwt documentation example at
# https://flask-jwt-extended.readthedocs.io/en/stable/refreshing_tokens/
@auth_bp.after_request
def refresh_expiring_jwts(response):
    try:
        exp_timestamp = get_jwt()["exp"]
        now = datetime.now(timezone.utc)
        target_timestamp = datetime.timestamp(now + timedelta(minutes=30))
        if target_timestamp > exp_timestamp:
            access_token = create_access_token(identity=get_jwt_identity())
        return response
    except (RuntimeError, KeyError):
        return response


# Checks if user credentials match a user entry in the DB.
# Returns user if correct username and password combination are passed
# Returns false otherwise.
@auth_bp.route("/login", methods=["GET", "POST"])
def AuthenticateUser():
    cur = conn.cursor()

    if request.method == "POST":
        data = json.loads(request.data)
        username = data["username"]
        p_word = data["password"]

        cur.execute("SELECT * from users where username = %(usr)s", {"usr": username})
        user = cur.fetchone()
        error = None

        if not user:
            error = "Invalid username"
        else:
            hashed_pword = user[4]
            if not check_password(p_word, hashed_pword):
                error = "Invalid password"

        if error == None:
            current_app.logger.info("Succesful login by user: %s", user[3])

            # set identity based on user email
            access_token = create_access_token(identity=user[2])
            return jsonify(access_token)

        current_app.logger.error(error)
        return jsonify({"err_msg": error}), 401


# endpoint for logging out.
# Taken from flask-jwt documentation example at
# https://flask-jwt-extended.readthedocs.io/en/stable/refreshing_tokens/
@auth_bp.route("/logout", methods=["GET", "POST"])
@jwt_required()
def logout():
    response = jsonify({"msg": "logout successful"})
    unset_jwt_cookies(response)
    return response


# Checks if entered password matches hashed password in the db.
def check_password(plain_password, hashed_password):
    return bcrypt.verify(plain_password, hashed_password)


# temp function to test connection with frontend
# fetches sample user from the db
@auth_bp.route("/user", methods=["GET", "POST"])
@jwt_required()
def fetch_user():
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users where email=%s", (get_jwt_identity(),))
    usr = cur.fetchone()
    return jsonify(usr)


# Endpoint for changing password.
# This function requires the jwt.
# It then checks:
# 1. that the current password provided matches the one stored
# 2. that the new password matches the requirements
# 3. that the new password and repeated password match
# If no error is encountered, the new password is hashed and updated in the database.
@auth_bp.route("/profile/password", methods=["PUT"])
@jwt_required()
def change_password():
    data = json.loads(request.data)
    response_code = 200
    error = None
    current_pwd = data["Current Password"]
    entered_pwd = data["New Password"]
    repeat_pwd = data["Repeat"]
    verify_jwt_in_request(optional=False)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT pass FROM users where email=%s", (get_jwt_identity(),))
    stored_pwd = (cur.fetchone())["pass"]
    if not check_password(current_pwd, stored_pwd):  # compare current with db password
        error = "Invalid password."
        response_code = 401
    if not IsValidPassword(entered_pwd):
        error = "Password too weak"
        response_code = 400
    if not entered_pwd == repeat_pwd:  # compare new and repeat password
        error = "Passwords do not match."
        response_code = 400
    if error == None:
        hashed_new_pwd = bcrypt.hash(entered_pwd)  # hashes password to add to db
        cur.execute(
            "UPDATE users SET pass=%s where email=%s",
            (
                hashed_new_pwd,
                get_jwt_identity(),
            ),
        )
        conn.commit()
        return (
            jsonify({"message": "Password changed successfully"}),
            response_code,
        )  # success
    else:
        return jsonify({"err_msg": error}), response_code  # error


@auth_bp.route("/profile/account", methods=["PUT"])
@jwt_required()
def change_account_details():
    data = json.loads(request.data)
    new_f_name = data["First Name"]
    new_l_name = data["Last Name"]
    new_username = data["Username"]
    verify_jwt_in_request(optional=False)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "UPDATE users SET first_name=%s, last_name=%s, username=%s where email=%s RETURNING *",
        (
            new_f_name,
            new_l_name,
            new_username,
            get_jwt_identity(),
        ),
    )
    conn.commit()
    updated_row = cur.fetchone()
    return jsonify(updated_row), 200
