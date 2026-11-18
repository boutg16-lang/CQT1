package com.example.oussamacutter.data

import com.example.oussamacutter.model.RiskLevel
import com.example.oussamacutter.model.SafetyActionMode
import kotlinx.coroutines.runBlocking
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

    @Test
    fun missingApiKeyReturnsNoApiKeyInsteadOfFakeClips() {
        GeminiService.setCustomApiKey("")

        val result = runBlocking {
            GeminiService.analyzeAndGenerateViralClips(
                sourceUrlOrTranscript = "نص عربي طويل بما يكفي لاجتياز فحص المدخل ويبدأ بحرف وليس برابط",
                targetClipsCount = 3,
                minDurationSec = 15,
                maxDurationSec = 60
            )
        }

        assertTrue("Expected NoApiKey but got $result", result is AnalysisResult.NoApiKey)
    }

    @Test
    fun urlInputIsRejectedInsteadOfFabricatingTranscript() {
        GeminiService.setCustomApiKey("")

        val result = runBlocking {
            GeminiService.analyzeAndGenerateViralClips(
                sourceUrlOrTranscript = "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                targetClipsCount = 3,
                minDurationSec = 15,
                maxDurationSec = 60
            )
        }

        assertTrue("Expected InvalidInput but got $result", result is AnalysisResult.InvalidInput)
    }
}
