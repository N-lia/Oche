package ai.oyi.device.data

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import ai.oyi.device.model.UsageSession

/**
 * Local store for foreground-app usage.
 *
 * Two tables:
 *  - daily_usage: per app / per day total foreground time (from UsageStatsManager)
 *  - sessions:    individual foreground intervals (from UsageStatsManager events)
 *
 * The phone is the source of truth; the server never stores usage history.
 */
class UsageRepository(context: Context) : SQLiteOpenHelper(
    context.applicationContext,
    DATABASE_NAME,
    null,
    DATABASE_VERSION,
) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE daily_usage (
                package_name TEXT NOT NULL,
                day TEXT NOT NULL,
                total_ms INTEGER NOT NULL,
                last_updated INTEGER NOT NULL,
                PRIMARY KEY (package_name, day)
            )
            """.trimIndent(),
        )
        db.execSQL(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                package_name TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                ended_at INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL
            )
            """.trimIndent(),
        )
        db.execSQL("CREATE INDEX idx_sessions_started ON sessions (started_at)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        db.execSQL("DROP TABLE IF EXISTS daily_usage")
        db.execSQL("DROP TABLE IF EXISTS sessions")
        onCreate(db)
    }

    /** Replaces the daily totals for one day with a fresh snapshot. */
    fun replaceDailyTotals(day: String, totals: Map<String, Long>, now: Long = System.currentTimeMillis()) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            db.delete("daily_usage", "day = ?", arrayOf(day))
            totals.forEach { (packageName, totalMs) ->
                if (totalMs <= 0L) return@forEach
                val values = ContentValues().apply {
                    put("package_name", packageName)
                    put("day", day)
                    put("total_ms", totalMs)
                    put("last_updated", now)
                }
                db.insertWithOnConflict("daily_usage", null, values, SQLiteDatabase.CONFLICT_REPLACE)
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    /** Replaces every session that starts inside [fromMillis] with [sessions]. */
    fun replaceSessionsSince(fromMillis: Long, sessions: List<UsageSession>) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            db.delete("sessions", "started_at >= ?", arrayOf(fromMillis.toString()))
            sessions.forEach { session ->
                val values = ContentValues().apply {
                    put("package_name", session.packageName)
                    put("started_at", session.startedAt)
                    put("ended_at", session.endedAt)
                    put("duration_ms", session.durationMs)
                }
                db.insertWithOnConflict("sessions", null, values, SQLiteDatabase.CONFLICT_REPLACE)
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    /** Top apps for one day, biggest first. */
    fun topApps(day: String, limit: Int = 10): List<Pair<String, Long>> {
        val out = mutableListOf<Pair<String, Long>>()
        readableDatabase.query(
            "daily_usage",
            arrayOf("package_name", "total_ms"),
            "day = ?",
            arrayOf(day),
            null,
            null,
            "total_ms DESC",
            limit.toString(),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                out.add(cursor.getString(0) to cursor.getLong(1))
            }
        }
        return out
    }

    /** Total foreground time for one package on one day. */
    fun totalFor(day: String, packageName: String): Long {
        readableDatabase.query(
            "daily_usage",
            arrayOf("total_ms"),
            "day = ? AND package_name = ?",
            arrayOf(day, packageName),
            null,
            null,
            null,
            "1",
        ).use { cursor ->
            return if (cursor.moveToFirst()) cursor.getLong(0) else 0L
        }
    }

    /** Most recent sessions, newest first. */
    fun recentSessions(limit: Int = 50): List<UsageSession> {
        val out = mutableListOf<UsageSession>()
        readableDatabase.query(
            "sessions",
            arrayOf("package_name", "started_at", "ended_at", "duration_ms"),
            null,
            null,
            null,
            null,
            "started_at DESC",
            limit.toString(),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                out.add(
                    UsageSession(
                        packageName = cursor.getString(0),
                        startedAt = cursor.getLong(1),
                        endedAt = cursor.getLong(2),
                        durationMs = cursor.getLong(3),
                    ),
                )
            }
        }
        return out
    }

    /** Drops history older than [beforeMillis] to keep the DB small. */
    fun prune(beforeMillis: Long) {
        val db = writableDatabase
        db.delete("sessions", "ended_at < ?", arrayOf(beforeMillis.toString()))
    }

    private companion object {
        const val DATABASE_NAME = "oyi_usage.db"
        const val DATABASE_VERSION = 1
    }
}
