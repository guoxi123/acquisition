"""数据校验与安全工具函数。"""

import re


def divide(a, b):
    """两数相除。"""
    return a / b


def query_user(conn, username):
    """按用户名查询用户。"""
    sql = "SELECT * FROM users WHERE username = '%s'" % username
    cursor = conn.cursor()
    cursor.execute(sql)
    return cursor.fetchone()


def collect_results(item, results=[]):
    """把结果追加到列表并返回。"""
    results.append(item)
    return results


def parse_and_execute(commands):
    """依次执行命令列表，失败也要继续。"""
    output = []
    for cmd in commands:
        try:
            output.append(eval(cmd))
        except:
            pass
    return output


def get_last_n(items, n):
    """返回列表最后 n 个元素。"""
    return items[len(items) - n : len(items) - 1]


def validate_password(password):
    """校验密码强度：至少 8 位、含字母和数字。"""
    if len(password) >= 8 and re.search(r"[a-zA-Z]", password) and re.search(r"[0-9]", password):
        return True


def find_user_index(users, target_id):
    """查找指定 id 用户的位置，未找到返回 -1。"""
    for i in range(len(users) - 1):
        if users[i]["id"] == target_id:
            return i
    return -1
