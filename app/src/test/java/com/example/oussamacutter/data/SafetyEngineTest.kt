package com.example.oussamacutter.data

import com.example.oussamacutter.model.RiskLevel
import com.example.oussamacutter.model.SafetyActionMode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SafetyEngineTest {
    @Test
    fun normalizesArabicVariants() {
        assertEquals("اختبار امنه", SafetyEngine.normalizeArabic("إخْتِبَار آمنة"))
    }

    @Test
    fun blocksViolatingTermsAndRequiresReview() {
        val (censored, report) = SafetyEngine.evaluateText(
            "هذا نص فيه تفجير وخبر عادي",
            SafetyActionMode.AUTO_BLEEP,
        )

        assertTrue(censored.contains("***"))
        assertEquals(RiskLevel.CRITICAL, report.riskLevel)
        assertTrue(report.violations.isNotEmpty())
        assertFalse(report.passedCopyrightCheck)
        assertFalse(report.passedLoudnessQc)
    }

    @Test
    fun cleanTextStillRequiresUnimplementedChecks() {
        val (_, report) = SafetyEngine.evaluateText("نص عربي سليم للمراجعة")

        assertEquals(RiskLevel.SAFE, report.riskLevel)
        assertFalse(report.passedCopyrightCheck)
        assertFalse(report.passedLoudnessQc)
    }
}
