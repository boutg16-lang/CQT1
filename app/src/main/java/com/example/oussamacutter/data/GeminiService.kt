package com.example.oussamacutter.data

import android.util.Log
import com.example.oussamacutter.model.ClipSegment
import com.example.oussamacutter.model.SafetyActionMode
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.util.*

sealed class AnalysisResult {
    data class Success(val clips: List<ClipSegment>) : AnalysisResult()
    data class ApiError(val message: String) : AnalysisResult()
    data class InvalidInput(val message: String) : AnalysisResult()
    object NoApiKey : AnalysisResult()
}

object GeminiService {

    private const val TAG = "GeminiService"
    private var customApiKey: String = ""

    fun setCustomApiKey(key: String) {
        customApiKey = key.trim()
    }

    fun getEffectiveApiKey(): String {
        // The optional user-provided key is memory-only; no key is bundled in the APK.
        return customApiKey
    }

    fun hasApiKey(): Boolean {
        return getEffectiveApiKey().isNotBlank()
    }

    suspend fun analyzeAndGenerateViralClips(
        sourceUrlOrTranscript: String,
        targetClipsCount: Int,
        minDurationSec: Int,
        maxDurationSec: Int
    ): AnalysisResult = withContext(Dispatchers.IO) {
        val input = sourceUrlOrTranscript.trim()
        if (input.isBlank()) {
            return@withContext AnalysisResult.InvalidInput("أدخل نص الفيديو (التفريغ النصي) قبل بدء التحليل.")
        }
        if (input.startsWith("http")) {
            return@withContext AnalysisResult.InvalidInput("المحتوى المدخل رابط. الصق نص الفيديو (التفريغ النصي) بدلاً من الرابط، فالتطبيق لا يستخرج النص من الروابط.")
        }
        val apiKey = getEffectiveApiKey()
        if (apiKey.isBlank()) {
            Log.d(TAG, "No API key configured; returning NoApiKey instead of fabricating clips.")
            return@withContext AnalysisResult.NoApiKey
        }

        try {
            val endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
            val prompt = """
                أنت خبير مونتاج وتحليل فيديوهات فيروسية على تيك توك ويوتيوب شورتس باللغة العربية.
                قم بتحليل المحتوى التالي واستخرج $targetClipsCount مقاطع فيروسية (بين $minDurationSec و $maxDurationSec ثانية).
                المحتوى:
                $input

                أعد النتيجة بصيغة JSON حصراً بهذا الهيكل:
                {
                  "clips": [
                    {
                      "title": "عنوان وصفي للمقطع",
                      "arabicHookTitle": "عنوان فيروزي جذاب ومثير للفضول يبدأ بإيموجي",
                      "durationSeconds": 30,
                      "viralScore": 95,
                      "hookStrength": 92,
                      "transcript": "نص المقطع كلمة بكلمة باللغة العربية بشكل دقيق"
                    }
                  ]
                }
            """.trimIndent()

            val requestBody = buildJsonObject {
                putJsonArray("contents") {
                    addJsonObject {
                        putJsonArray("parts") {
                            addJsonObject {
                                put("text", prompt)
                            }
                        }
                    }
                }
                putJsonObject("generationConfig") {
                    put("responseMimeType", "application/json")
                    put("temperature", 0.7)
                }
            }.toString()

            val url = URL(endpoint)
            val connection = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
                setRequestProperty("x-goog-api-key", apiKey)
                doOutput = true
                doInput = true
                connectTimeout = 15000
                readTimeout = 25000
            }

            OutputStreamWriter(connection.outputStream, "UTF-8").use { writer ->
                writer.write(requestBody)
                writer.flush()
            }

            val responseCode = connection.responseCode
            if (responseCode == HttpURLConnection.HTTP_OK) {
                val responseText = BufferedReader(InputStreamReader(connection.inputStream, "UTF-8")).use {
                    it.readText()
                }

                val jsonResponse = Json.parseToJsonElement(responseText)
                val candidates = jsonResponse.jsonObject["candidates"]?.jsonArray
                val firstCandidate = candidates?.firstOrNull()?.jsonObject
                val parts = firstCandidate?.get("content")?.jsonObject?.get("parts")?.jsonArray
                val jsonText = parts?.firstOrNull()?.jsonObject?.get("text")?.jsonPrimitive?.content ?: ""

                val parsedRoot = Json.parseToJsonElement(jsonText).jsonObject
                val clipsArray = parsedRoot["clips"]?.jsonArray

                if (clipsArray == null || clipsArray.isEmpty()) {
                    Log.e(TAG, "Gemini response missing a non-empty clips array")
                    return@withContext AnalysisResult.ApiError("استجابة Gemini لا تحتوي على مقاطع صالحة. أعد المحاولة.")
                }
                val hasMissingTranscript = clipsArray.any {
                    it.jsonObject["transcript"]?.jsonPrimitive?.contentOrNull.isNullOrBlank()
                }
                if (hasMissingTranscript) {
                    Log.e(TAG, "Gemini response missing transcript for at least one clip")
                    return@withContext AnalysisResult.ApiError("استجابة Gemini ناقصة (نص المقطع مفقود). أعد المحاولة.")
                }
                return@withContext AnalysisResult.Success(
                    clipsArray.mapIndexed { index, element ->
                        val clipObj = element.jsonObject
                        val title = clipObj["title"]?.jsonPrimitive?.content ?: "مقطع فيروزي ${index + 1}"
                        val hook = clipObj["arabicHookTitle"]?.jsonPrimitive?.content ?: "🔥 شاهد هذا المقطع المفاجئ!"
                        val duration = clipObj["durationSeconds"]?.jsonPrimitive?.intOrNull ?: 35
                        val viral = clipObj["viralScore"]?.jsonPrimitive?.intOrNull
                        val hookScore = clipObj["hookStrength"]?.jsonPrimitive?.intOrNull
                        val transcript = clipObj["transcript"]?.jsonPrimitive?.content ?: ""

                        buildClipFromRealData(
                            id = "gemini_clip_${System.currentTimeMillis()}_$index",
                            title = title,
                            arabicHookTitle = hook,
                            duration = duration,
                            viralScore = viral,
                            hookStrength = hookScore,
                            rawTranscript = transcript
                        )
                    }
                )
            } else {
                Log.e(TAG, "Gemini API error code: $responseCode")
                return@withContext AnalysisResult.ApiError("فشل طلب Gemini (رمز $responseCode). تحقق من المفتاح ثم أعد المحاولة.")
            }
        } catch (e: Exception) {
            Log.e(TAG, "Exception during Gemini API call", e)
            return@withContext AnalysisResult.ApiError("تعذر الاتصال بخدمة Gemini. تحقق من الاتصال بالإنترنت وأعد المحاولة.")
        }
    }

    private fun buildClipFromRealData(
        id: String,
        title: String,
        arabicHookTitle: String,
        duration: Int,
        viralScore: Int?,
        hookStrength: Int?,
        rawTranscript: String
    ): ClipSegment {
        val (censored, report) = SafetyEngine.evaluateText(rawTranscript, SafetyActionMode.AUTO_BLEEP)
        val words = censored.split("\\s+".toRegex()).filter { it.isNotBlank() }.mapIndexed { i, w ->
            com.example.oussamacutter.model.SubtitleWord(
                id = "${id}_w_$i",
                text = w,
                startMs = i * 500L,
                endMs = (i * 500L) + 480L,
                isBleeped = w == "***",
                isHookKeyword = i < 2
            )
        }

        return ClipSegment(
            id = id,
            title = title,
            arabicHookTitle = arabicHookTitle,
            durationSeconds = duration,
            viralScore = viralScore,
            hookStrength = hookStrength,
            words = words,
            safetyReport = report,
            // AI output must be reviewed explicitly before publishing.
            isApproved = false,
            targetPlatform = "TikTok & Shorts (9:16)"
        )
    }
}
