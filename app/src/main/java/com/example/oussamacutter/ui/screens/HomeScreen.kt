package com.example.oussamacutter.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.oussamacutter.data.ProjectRepository
import com.example.oussamacutter.model.ClipSegment
import com.example.oussamacutter.ui.theme.*

@Composable
fun HomeScreen(
    onNavigateToCreate: () -> Unit,
    onNavigateToSafety: () -> Unit,
    onNavigateToReview: (String?) -> Unit,
    onNavigateToPublish: () -> Unit,
    modifier: Modifier = Modifier
) {
    val systemHealth by ProjectRepository.systemHealth.collectAsState()
    val clips by ProjectRepository.clips.collectAsState()
    val queue by ProjectRepository.batchQueue.collectAsState()

    LazyColumn(
        modifier = modifier
            .fillMaxSize()
            .background(DarkBg)
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        contentPadding = PaddingValues(top = 16.dp, bottom = 96.dp)
    ) {
        // Hero Header Banner
        item {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(20.dp))
                    .background(
                        Brush.linearGradient(
                            listOf(
                                Color(0xFF1E1B4B),
                                Color(0xFF312E81),
                                Color(0xFF0F172A)
                            )
                        )
                    )
                    .border(1.dp, PrimaryLight.copy(alpha = 0.2f), RoundedCornerShape(20.dp))
                    .padding(20.dp)
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Surface(
                            color = PrimaryIndigo.copy(alpha = 0.2f),
                            shape = RoundedCornerShape(12.dp)
                        ) {
                            Text(
                                text = "v8.0 ULTIMATE PRO",
                                color = PrimaryLight,
                                style = MaterialTheme.typography.labelMedium,
                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp)
                            )
                        }

                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(8.dp)
                                    .clip(CircleShape)
                                    .background(SuccessGreen)
                            )
                            Text(
                                text = "محرك المونتاج نشط",
                                color = SuccessGreen,
                                style = MaterialTheme.typography.bodyMedium,
                                fontWeight = FontWeight.Bold
                            )
                        }
                    }

                    Text(
                        text = "OUSSAMA Cutter",
                        style = MaterialTheme.typography.headlineLarge,
                        color = TextPrimary
                    )

                    Text(
                        text = "استوديو الذكاء الاصطناعي العربي لتحويل الفيديوهات الطويلة إلى مقاطع فيروسية مع حماية ضد الإنذارات وتتبع الوجوه.",
                        style = MaterialTheme.typography.bodyLarge,
                        color = TextSecondary
                    )

                    Spacer(modifier = Modifier.height(6.dp))

                    Button(
                        onClick = onNavigateToCreate,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = PrimaryIndigo
                        ),
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(48.dp)
                            .testTag("home_create_button")
                    ) {
                        Icon(Icons.Default.Add, contentDescription = null, tint = TextPrimary)
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = "بدء مشروع تقطيع جديد",
                            style = MaterialTheme.typography.titleMedium,
                            color = TextPrimary
                        )
                    }
                }
            }
        }

        // Metrics Grid
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                MetricCard(
                    title = "المقاطع الجاهزة",
                    value = "${clips.size}",
                    icon = Icons.Default.MovieFilter,
                    iconColor = AccentCyan,
                    modifier = Modifier.weight(1f)
                )
                MetricCard(
                    title = "متوسط الفيروسية",
                    value = "93%",
                    icon = Icons.Default.TrendingUp,
                    iconColor = AccentGold,
                    modifier = Modifier.weight(1f)
                )
                MetricCard(
                    title = "فحص الأمان",
                    value = "100%",
                    icon = Icons.Default.Shield,
                    iconColor = SuccessGreen,
                    modifier = Modifier.weight(1f)
                )
            }
        }

        // Quick Feature Links
        item {
            Text(
                text = "الخدمات الأساسية",
                style = MaterialTheme.typography.titleLarge,
                color = TextPrimary,
                modifier = Modifier.padding(vertical = 4.dp)
            )
        }

        item {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                QuickActionRow(
                    title = "🛡️ حامي المحتوى من المخالفات (Anti-Strike)",
                    subtitle = "فحص الكلمات المحظورة، كتم الصوت التلقائي، ومنع الإنذارات",
                    icon = Icons.Default.Security,
                    color = AccentCyan,
                    onClick = onNavigateToSafety,
                    tag = "quick_safety"
                )
                QuickActionRow(
                    title = "👀 مراجعة وتعديل الترجمة الديناميكية",
                    subtitle = "نمط هورموزي، إبراز الكلمات كلمة بكلمة، وتصدير XML لـ Premiere",
                    icon = Icons.Default.EditNote,
                    color = AccentPurple,
                    onClick = { onNavigateToReview(clips.firstOrNull()?.id) },
                    tag = "quick_review"
                )
                QuickActionRow(
                    title = "🚀 جدولة ونشر المقاطع على المنصات",
                    subtitle = "توليد العناوين الفيروسية، وسوم TikTok، ومزامنة توقيت النشر",
                    icon = Icons.Default.ScheduleSend,
                    color = AccentGold,
                    onClick = onNavigateToPublish,
                    tag = "quick_publish"
                )
            }
        }

        // System Health & Engine Diagnostics
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(16.dp)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "تشخيص المحرك والموارد",
                            style = MaterialTheme.typography.titleMedium,
                            color = TextPrimary
                        )
                        Icon(Icons.Default.Speed, contentDescription = null, tint = PrimaryLight)
                    }

                    HorizontalDivider(color = DarkSurfaceVariant)

                    DiagnosticRow("تسريع المعالجة (GPU)", systemHealth.gpuModel, SuccessGreen)
                    DiagnosticRow("محرك التفريغ الصوتي", systemHealth.transcriptionBackend, TextPrimary)
                    DiagnosticRow("قاموس الأمان ومكافحة الانتهاكات", "${systemHealth.activeLexiconTermsCount} مصطلح نشط", AccentCyan)
                    DiagnosticRow("تحليل هوك Gemini", systemHealth.geminiStatus, PrimaryLight)
                    DiagnosticRow("مستوى الصوت المعياري (Audio QC)", "-14.0 LUFS (YouTube Shorts standard)", SuccessGreen)
                }
            }
        }

        // Studio Projects Library section
        item {
            val projects by ProjectRepository.projects.collectAsState()
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "مشاريع الاستوديو (مكتبة الفيديوهات)",
                        style = MaterialTheme.typography.titleLarge,
                        color = TextPrimary
                    )
                    Text(
                        text = "${projects.size} مشاريع",
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextSecondary
                    )
                }

                projects.forEach { proj ->
                    Card(
                        colors = CardDefaults.cardColors(containerColor = DarkCard),
                        shape = RoundedCornerShape(14.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Row(
                            modifier = Modifier.padding(14.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(12.dp)
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(44.dp)
                                    .clip(RoundedCornerShape(10.dp))
                                    .background(PrimaryIndigo.copy(alpha = 0.15f)),
                                contentAlignment = Alignment.Center
                            ) {
                                Icon(Icons.Default.VideoLibrary, contentDescription = null, tint = PrimaryLight)
                            }

                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = proj.title,
                                    style = MaterialTheme.typography.titleSmall,
                                    color = TextPrimary,
                                    maxLines = 1
                                )
                                Text(
                                    text = "${proj.durationFormatted} • ${proj.clipsCount} مقاطع • ${proj.dateCreated}",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = TextSecondary
                                )
                            }

                            Surface(
                                color = SuccessGreen.copy(alpha = 0.2f),
                                shape = RoundedCornerShape(6.dp)
                            ) {
                                Text(
                                    text = "آمن",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = SuccessGreen,
                                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                                )
                            }
                        }
                    }
                }
            }
        }

        // Recent Clips Header
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "أحدث المقاطع المستخرجة",
                    style = MaterialTheme.typography.titleLarge,
                    color = TextPrimary
                )
                Text(
                    text = "${clips.size} مقطع",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary
                )
            }
        }

        // Recent Clips List
        items(clips) { clip ->
            ClipCardCompact(
                clip = clip,
                onClick = { onNavigateToReview(clip.id) }
            )
        }
    }
}

@Composable
fun MetricCard(
    title: String,
    value: String,
    icon: ImageVector,
    iconColor: Color,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = DarkCard),
        shape = RoundedCornerShape(14.dp)
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp)
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = iconColor,
                modifier = Modifier.size(20.dp)
            )
            Text(
                text = value,
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
                color = TextPrimary
            )
            Text(
                text = title,
                style = MaterialTheme.typography.bodyMedium,
                color = TextSecondary,
                maxLines = 1
            )
        }
    }
}

@Composable
fun QuickActionRow(
    title: String,
    subtitle: String,
    icon: ImageVector,
    color: Color,
    onClick: () -> Unit,
    tag: String
) {
    Surface(
        onClick = onClick,
        color = DarkCard,
        shape = RoundedCornerShape(14.dp),
        modifier = Modifier
            .fillMaxWidth()
            .testTag(tag)
    ) {
        Row(
            modifier = Modifier.padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            Box(
                modifier = Modifier
                    .size(42.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(color.copy(alpha = 0.15f)),
                contentAlignment = Alignment.Center
            ) {
                Icon(icon, contentDescription = null, tint = color)
            }

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleMedium,
                    color = TextPrimary
                )
                Text(
                    text = subtitle,
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary
                )
            }

            Icon(
                Icons.Default.ChevronLeft,
                contentDescription = null,
                tint = TextMuted
            )
        }
    }
}

@Composable
fun DiagnosticRow(title: String, value: String, statusColor: Color) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(title, style = MaterialTheme.typography.bodyMedium, color = TextSecondary)
        Text(value, style = MaterialTheme.typography.bodyMedium, color = statusColor, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
fun ClipCardCompact(
    clip: ClipSegment,
    onClick: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .testTag("clip_item_${clip.id}"),
        colors = CardDefaults.cardColors(containerColor = DarkSurfaceVariant),
        shape = RoundedCornerShape(14.dp)
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Box(
                modifier = Modifier
                    .size(width = 54.dp, height = 74.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(DarkBg),
                contentAlignment = Alignment.Center
            ) {
                Icon(Icons.Default.PlayCircle, contentDescription = null, tint = PrimaryLight)
                Text(
                    text = "${clip.durationSeconds}s",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary,
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = 4.dp)
                )
            }

            Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    text = clip.arabicHookTitle,
                    style = MaterialTheme.typography.titleMedium,
                    color = TextPrimary,
                    maxLines = 1
                )
                Text(
                    text = clip.title,
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary,
                    maxLines = 1
                )

                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Surface(
                        color = PrimaryIndigo.copy(alpha = 0.2f),
                        shape = RoundedCornerShape(6.dp)
                    ) {
                        Text(
                            text = "Viral Score: ${clip.viralScore}",
                            style = MaterialTheme.typography.labelSmall,
                            color = PrimaryLight,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                        )
                    }

                    Surface(
                        color = Color(clip.safetyReport.riskLevel.colorHex).copy(alpha = 0.2f),
                        shape = RoundedCornerShape(6.dp)
                    ) {
                        Text(
                            text = clip.safetyReport.riskLevel.labelAr,
                            style = MaterialTheme.typography.labelSmall,
                            color = Color(clip.safetyReport.riskLevel.colorHex),
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                        )
                    }
                }
            }

            Icon(
                Icons.Default.ChevronLeft,
                contentDescription = null,
                tint = TextMuted
            )
        }
    }
}
