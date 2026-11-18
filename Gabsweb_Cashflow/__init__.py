"""Project package.

Django's MySQL backend imports ``MySQLdb``. Prefer the compiled ``mysqlclient``
package when the hosting account provides it; otherwise fall back to the pure
Python PyMySQL driver so the same MySQL 8.4 database works on shared hosting
without a compiler toolchain.
"""

try:  # pragma: no cover - depends on the installed database driver
    import MySQLdb  # noqa: F401
except ImportError:  # pragma: no cover
    try:
        import pymysql

        pymysql.install_as_MySQLdb()
    except ImportError:
        pass
