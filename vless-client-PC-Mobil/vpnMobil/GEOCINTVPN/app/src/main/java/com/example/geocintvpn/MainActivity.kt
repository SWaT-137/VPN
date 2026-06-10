package com.example.geocintvpn

import android.app.Activity
import android.app.ActivityManager
import android.content.Context
import android.net.TrafficStats
import android.net.Uri
import android.net.VpnService
import android.os.Bundle
import android.os.Process
import android.util.Base64
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.outlined.PowerSettingsNew
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import kotlin.collections.mutableMapOf
// глобал перменные
val DarkBg = Color(0xFF26252D)
val DarkSurface = Color(0xFF2E2D38)
val GrayText = Color(0xFFA0A0B0)
val GreenPrimary = Color(0xFF4CAF50)


// точка входа, используем compose а не XML разметку
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme(
                    primary = GreenPrimary,
                    background = DarkBg,
                    surface = DarkSurface,
                    onPrimary = Color.White,
                    onBackground = GrayText,
                    onSurface = Color.White
                )
            ) {
                Surface(modifier = Modifier.fillMaxSize(), color = DarkBg) {
                    VPNScreen()
                }
            }
        }
    }
}
// логика и визуал
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun VPNScreen() {
    val context = LocalContext.current
    val activity = context as? Activity ?: return
    val clipboardManager = LocalClipboardManager.current // используем clipboardManager для того что бы вставить

    val backgroundBrush = Brush.verticalGradient(
        colors = listOf(Color(0xFF1A1A2E), DarkBg)
    )

    val prefs = context.getSharedPreferences("vpn_prefs", Context.MODE_PRIVATE) // сохранение настроек с помощью внутренй памяти андроед
    var subLink by remember { mutableStateOf(prefs.getString("sub_link", "") ?: "") }

    var linkHistory by remember {
        mutableStateOf(prefs.getStringSet("link_history", emptySet())?.toList() ?: emptyList())
    }
// переменные
    var isConnected by remember { mutableStateOf(false) }
    var statusText by remember { mutableStateOf("Отключено") }
    var pendingConfig by remember { mutableStateOf<String?>(null) }

    var showSettingsDialog by remember { mutableStateOf(false) }
    var showStatsDialog by remember { mutableStateOf(false) }
    var showCustomToast by remember { mutableStateOf(false) }
    var customToastText by remember { mutableStateOf("") }

    var sessionSeconds by remember { mutableIntStateOf(0) }
    var baseRxBytes by remember { mutableLongStateOf(0L) }
    var baseTxBytes by remember { mutableLongStateOf(0L) }
    var sessionDown by remember { mutableLongStateOf(0L) }
    var sessionUp by remember { mutableLongStateOf(0L) }

    var totalSeconds by remember { mutableLongStateOf(prefs.getLong("stat_total_seconds", 0L)) }
    var totalDown by remember { mutableLongStateOf(prefs.getLong("stat_total_down", 0L)) }
    var totalUp by remember { mutableLongStateOf(prefs.getLong("stat_total_up", 0L)) }
    var totalConns by remember { mutableIntStateOf(prefs.getInt("stat_total_conns", 0)) }

    val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
    val coroutineScope = rememberCoroutineScope()

// грамотное завершение: сохраняем статистику при свайпе приложения или закрытии
    DisposableEffect(Unit) {
        onDispose {
            if (isConnected) {
                val editor = prefs.edit()
                editor.putLong("stat_total_seconds", totalSeconds + sessionSeconds)
                editor.putLong("stat_total_down", totalDown + sessionDown)
                editor.putLong("stat_total_up", totalUp + sessionUp)
                editor.putBoolean("is_vpn_running", false) // сбрасываем флаг
                editor.apply()
            }
        }
    }

    // управление историей
    fun saveToHistory(link: String) {
        if (link.isBlank()) return
        val updatedHistory = (listOf(link) + linkHistory).distinct().take(5)
        linkHistory = updatedHistory
        prefs.edit().putStringSet("link_history", updatedHistory.toSet()).apply()
    }

    fun removeFromHistory(linkToRemove: String) {
        val updatedHistory = linkHistory.filter { it != linkToRemove }
        linkHistory = updatedHistory
        prefs.edit().putStringSet("link_history", updatedHistory.toSet()).apply()
    }
// проверка запущен ли впн и если запущен то включаем экран с подключенно (стабильная версия без устаревших API)
    LaunchedEffect(Unit) {
        // используем флаг из памяти вместо крашащегося getRunningServices для стабильности
        if (prefs.getBoolean("is_vpn_running", false)) {
            isConnected = true
            statusText = "Подключено"
            baseRxBytes = try { TrafficStats.getUidRxBytes(Process.myUid()) } catch (e: Exception) { 0L }
            baseTxBytes = try { TrafficStats.getUidTxBytes(Process.myUid()) } catch (e: Exception) { 0L }
        }
    }
// проверка статистики и ее обновление (добавлена защита от крашей на некоторых прошивках)
    LaunchedEffect(isConnected) {
        if (isConnected) {
            baseRxBytes = try { TrafficStats.getUidRxBytes(Process.myUid()) } catch (e: Exception) { 0L }
            baseTxBytes = try { TrafficStats.getUidTxBytes(Process.myUid()) } catch (e: Exception) { 0L }
            while (true) {
                delay(1000L)
                sessionSeconds++
                val currentRx = try { TrafficStats.getUidRxBytes(Process.myUid()) } catch (e: Exception) { -1L }
                val currentTx = try { TrafficStats.getUidTxBytes(Process.myUid()) } catch (e: Exception) { -1L }
                if (currentRx > 0 && baseRxBytes > 0) sessionDown = currentRx - baseRxBytes
                if (currentTx > 0 && baseTxBytes > 0) sessionUp = currentTx - baseTxBytes
            }
        } else {
            sessionSeconds = 0
            sessionDown = 0L
            sessionUp = 0L
        }
    }
// запрашиваене разрешение на создание впна
    val vpnPermissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            pendingConfig?.let {
                startVpnConnection(activity, it, prefs) { success ->
                    if (success) {
                        totalConns++
                        prefs.edit().putInt("stat_total_conns", totalConns).apply()
                        saveToHistory(subLink)

                        isConnected = true
                        statusText = "Подключено"
                    } else {
                        statusText = "Ошибка ядра"
                        isConnected = false
                    }
                }
                pendingConfig = null
            }
        } else {
            statusText = "Отклонено"
            isConnected = false
        }
    }
    // всплывающие снизу окошечко кастомное
    fun showMyToast(message: String) {
        customToastText = message
        showCustomToast = true
        coroutineScope.launch {
            delay(2000)
            showCustomToast = false
        }
    }
    // включение выключение и записывание в общую статиску
    fun onToggle(checked: Boolean) {
        if (checked) {
            if (subLink.isBlank()) {
                showMyToast("\uD83D\uDD17 Откройте меню и укажите ссылку \uD83D\uDD17")
                return
            }
            statusText = "Подключение (настройка маршрутизации)..."
            CoroutineScope(Dispatchers.IO).launch {
                try {
                    val vlessConfig = MarzbanApi.getConfigFromInput(subLink.trim())
                    withContext(Dispatchers.Main) {
                        val vpnIntent = VpnService.prepare(activity)
                        if (vpnIntent != null) {
                            pendingConfig = vlessConfig
                            vpnPermissionLauncher.launch(vpnIntent)
                        } else {
                            startVpnConnection(activity, vlessConfig, prefs) { success ->
                                if (success) {
                                    totalConns++
                                    prefs.edit().putInt("stat_total_conns", totalConns).apply()
                                    saveToHistory(subLink)

                                    isConnected = true
                                    statusText = "Подключено"
                                }
                            }
                        }
                    }
                } catch (e: Exception) {
                    withContext(Dispatchers.Main) {
                        statusText = "Ошибка: ${e.message}"
                        isConnected = false
                    }
                }
            }
        } else {
            totalSeconds += sessionSeconds
            totalDown += sessionDown
            totalUp += sessionUp

            prefs.edit()
                .putLong("stat_total_seconds", totalSeconds)
                .putLong("stat_total_down", totalDown)
                .putLong("stat_total_up", totalUp)
                .putBoolean("is_vpn_running", false) // грамотное завершение: снимаем флаг
                .apply()

            dev.dev7.lib.v2ray.V2rayController.stopV2ray(activity)
            isConnected = false
            statusText = "Отключено"
        }
    }

    val sessionTimeString = String.format("%02d:%02d:%02d", sessionSeconds / 3600, (sessionSeconds % 3600) / 60, sessionSeconds % 60)
// построение графики с помощью Compose
    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet(
                drawerContainerColor = DarkSurface
            ) {
                Spacer(Modifier.height(16.dp))
                Text("Меню", modifier = Modifier.padding(start = 20.dp, bottom = 10.dp), color = Color.White, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                HorizontalDivider(color = Color.Gray.copy(alpha = 0.3f))

                NavigationDrawerItem(
                    label = { Text("⚙️ Настройки ⚙️", color = Color.White, fontWeight = FontWeight.Medium) },
                    selected = false,
                    onClick = {
                        coroutineScope.launch { drawerState.close() }
                        if (isConnected) {
                            showMyToast("⚠️ Сначала выключите VPN")
                        } else {
                            showSettingsDialog = true
                        }
                    },
                    modifier = Modifier.padding(horizontal = 12.dp)
                )
                NavigationDrawerItem(
                    label = { Text("📊 Статистика 📊", color = Color.White, fontWeight = FontWeight.Medium) },
                    selected = false,
                    onClick = {
                        coroutineScope.launch { drawerState.close() }
                        showStatsDialog = true
                    },
                    modifier = Modifier.padding(horizontal = 12.dp)
                )
            }
        }
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(backgroundBrush)
        ) {
            IconButton(
                onClick = { coroutineScope.launch { drawerState.open() } },
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .padding(start = 8.dp, top = 16.dp)
            ) {
                Icon(Icons.Filled.Menu, contentDescription = "Открыть меню", tint = Color.White)
            }

            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(top = 60.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                val buttonColor by animateColorAsState(
                    targetValue = if (isConnected) GreenPrimary else Color(0xFF3A3A47),
                    animationSpec = tween(durationMillis = 300),
                    label = "buttonColor"
                )

                Surface(
                    onClick = { onToggle(!isConnected) },
                    modifier = Modifier.size(200.dp),
                    shape = CircleShape,
                    color = buttonColor,
                    shadowElevation = if (isConnected) 16.dp else 4.dp
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(
                            imageVector = Icons.Outlined.PowerSettingsNew,
                            contentDescription = "Включить VPN",
                            tint = Color.White,
                            modifier = Modifier.size(96.dp)
                        )
                    }
                }

                Spacer(modifier = Modifier.height(20.dp))

                Text(
                    text = statusText,
                    color = if (isConnected) GreenPrimary else GrayText,
                    fontSize = 24.sp,
                    textAlign = TextAlign.Center,
                    fontWeight = FontWeight.Medium
                )

                Spacer(modifier = Modifier.height(20.dp))

                Card(
                    modifier = Modifier.padding(horizontal = 40.dp),
                    colors = CardDefaults.cardColors(containerColor = DarkSurface),
                    shape = RoundedCornerShape(16.dp)
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text("\uD83D\uDCC9 Полученно: ${formatBytes(sessionDown)} \uD83D\uDCC9", color = GrayText, fontSize = 14.sp)
                        Text("\uD83D\uDCC8 Отправлено: ${formatBytes(sessionUp)} \uD83D\uDCC8", color = GrayText, fontSize = 14.sp)
                        Text("⏳ Время сессии: $sessionTimeString ⏳", color = GrayText, fontSize = 14.sp)
                    }
                }
            }

            if (showCustomToast) {
                Card(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = 32.dp),
                    shape = RoundedCornerShape(12.dp),
                    colors = CardDefaults.cardColors(containerColor = Color(0xFF444444))
                ) {
                    Text(
                        text = customToastText,
                        modifier = Modifier.padding(horizontal = 24.dp, vertical = 12.dp),
                        color = Color.White,
                        fontSize = 14.sp,
                        textAlign = TextAlign.Center
                    )
                }
            }
        }
    }


    if (showSettingsDialog) {
        AlertDialog(
            onDismissRequest = { showSettingsDialog = false },
            containerColor = DarkSurface,
            shape = RoundedCornerShape(16.dp),
            title = { Text("⚙️ Настройки", color = Color.White, fontWeight = FontWeight.Bold) },
            text = {
                Column(
                    modifier = Modifier.verticalScroll(rememberScrollState())
                ) {
                    OutlinedTextField(
                        value = subLink,
                        onValueChange = { subLink = it },
                        label = { Text("VLESS ссылка или Подписка") },
                        singleLine = false,
                        maxLines = 3,
                        modifier = Modifier.fillMaxWidth(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = GreenPrimary,
                            unfocusedBorderColor = Color(0xFF555555),
                            cursorColor = GreenPrimary,
                            focusedLabelColor = GreenPrimary,
                            unfocusedTextColor = GrayText
                        )
                    )

                    Spacer(modifier = Modifier.height(8.dp))

                    TextButton(
                        onClick = {
                            clipboardManager.getText()?.text?.let { subLink = it }
                            showMyToast("✅ Вставлено ✅")
                        },
                        modifier = Modifier.align(Alignment.End)
                    ) {
                        Text("📋 Вставить 📋", color = GreenPrimary)
                    }

                    if (linkHistory.isNotEmpty()) {
                        Spacer(modifier = Modifier.height(16.dp))
                        HorizontalDivider(color = Color.Gray.copy(alpha = 0.3f))
                        Spacer(modifier = Modifier.height(8.dp))
                        Text("🔗 Недавние:", color = GrayText, fontSize = 14.sp, fontWeight = FontWeight.Bold)

                        linkHistory.forEach { historyLink ->
                            Row(
                                modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 2.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                TextButton(
                                    onClick = { subLink = historyLink },
                                    modifier = Modifier.weight(1f)
                                ) {
                                    Text(
                                        text = if (historyLink.length > 45) historyLink.substring(0, 45) + "..." else historyLink,
                                        color = Color.White,
                                        fontSize = 13.sp,
                                        textAlign = TextAlign.Start,
                                        modifier = Modifier.fillMaxWidth()
                                    )
                                }

                                IconButton(
                                    onClick = { removeFromHistory(historyLink) },
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.Close,
                                        contentDescription = "Удалить",
                                        tint = GrayText,
                                        modifier = Modifier.size(18.dp)
                                    )
                                }
                            }
                        }
                    }

                    // Кнопка перенесена сюда
                    Spacer(modifier = Modifier.height(24.dp))
                    HorizontalDivider(color = Color.Gray.copy(alpha = 0.3f))
                    Spacer(modifier = Modifier.height(16.dp))

                    TextButton(
                        onClick = {
                            showSettingsDialog = false // закрываем настройки перед убийством
                            prefs.edit().putBoolean("is_vpn_running", false).apply()
                            android.os.Process.killProcess(android.os.Process.myPid())
                        },
                        modifier = Modifier.fillMaxWidth(),
                        colors = ButtonDefaults.textButtonColors(contentColor = Color(0xFFFF5252))
                    ) {
                        Text("\uD83D\uDEA8Починка приложения\uD83D\uDEA8", fontSize = 16.sp)
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    prefs.edit().putString("sub_link", subLink).apply()
                    showSettingsDialog = false
                    showMyToast("\uD83D\uDCBE Сохранено \uD83D\uDCBE")
                }) {
                    Text("✅ Сохранить ✅", color = Color.White)
                }
            },
            dismissButton = {
                TextButton(onClick = { showSettingsDialog = false }) {
                    Text("❌ Отмена ❌", color = GrayText)
                }
            }
        )
    }

    if (showStatsDialog) {
        AlertDialog(
            onDismissRequest = { showStatsDialog = false },
            containerColor = DarkSurface,
            shape = RoundedCornerShape(16.dp),
            title = { Text("📊 Общая статистика", color = Color.White, fontWeight = FontWeight.Bold) },
            text = {
                Column {
                    Text("\uD83C\uDF10 Количество подключений: $totalConns \uD83C\uDF10", color = Color.White, fontSize = 14.sp)
                    Spacer(modifier = Modifier.height(8.dp))
                    Text("\uD83D\uDCC9 Всего загружено: ${formatBytes(totalDown)} \uD83D\uDCC9", color = Color.White, fontSize = 14.sp)
                    Spacer(modifier = Modifier.height(8.dp))
                    Text("\uD83D\uDCC8 Всего отправлено: ${formatBytes(totalUp)} \uD83D\uDCC8", color = Color.White, fontSize = 14.sp)
                    Spacer(modifier = Modifier.height(8.dp))
                    Text("⏳ Общее время: ${formatTotalTime(totalSeconds)} ⏳", color = Color.White, fontSize = 14.sp)
                }
            },
            confirmButton = {
                TextButton(onClick = { showStatsDialog = false }) {
                    Text("Закрыть", color = GrayText)
                }
            }
        )
    }
}


//  функции считалочки и переводители
private fun formatBytes(bytes: Long): String {
    return when {
        bytes < 1024 -> "$bytes Б"
        bytes < 1024 * 1024 -> String.format("%.1f КБ", bytes / 1024.0)
        bytes < 1024 * 1024 * 1024 -> String.format("%.1f МБ", bytes / (1024.0 * 1024.0))
        else -> String.format("%.2f ГБ", bytes / (1024.0 * 1024.0 * 1024.0))
    }
}

private fun formatTotalTime(totalSecs: Long): String {
    val days = totalSecs / 86400
    val hours = (totalSecs % 86400) / 3600
    val minutes = (totalSecs % 3600) / 60
    val seconds = totalSecs % 60

    return if (days > 0) {
        String.format("%d д. %02d:%02d:%02d", days, hours, minutes, seconds)
    } else {
        String.format("%02d:%02d:%02d", hours, minutes, seconds)
    }
}
// передача в системные настройки информации о работе (добавлен prefs для флага запуска)
private fun startVpnConnection(activity: Activity, vlessLink: String, prefs: android.content.SharedPreferences, onResult: (Boolean) -> Unit) {
    try {
        val finalConfig = buildSplitTunnelConfig(vlessLink)
        dev.dev7.lib.v2ray.V2rayController.startV2ray(activity, "GEOCINTVPN", finalConfig, null)
        prefs.edit().putBoolean("is_vpn_running", true).apply() // сохраняем флаг что мы запущены
        onResult(true)
    } catch (e: Exception) {
        e.printStackTrace()
        onResult(false)
    }
}
// логика генерации конфигурации (парсит чистую ссылку без закодированных строк)
private fun buildSplitTunnelConfig(vlessLink: String): String {
    val uri = android.net.Uri.parse(vlessLink)
    val uuid = uri.userInfo ?: ""
    val host = uri.host ?: ""
    val port = uri.port

    val params = mutableMapOf<String, String>()
    uri.queryParameterNames?.forEach { key ->
        uri.getQueryParameter(key)?.let { params[key] = it }
    }

    val network = params.getOrElse("type") { "tcp" }
    val security = params.getOrElse("security") { "" }
    val flow = params.getOrElse("flow") { "" }

    val streamSettings = JSONObject()
    streamSettings.put("network", network)
    streamSettings.put("security", security)
    if (security == "reality") {
        val realitySettings = JSONObject()
        realitySettings.put("serverName", params.getOrElse("sni") { "" })
        realitySettings.put("fingerprint", params.getOrElse("fp") { "chrome" })
        realitySettings.put("publicKey", params.getOrElse("pbk") { "" })
        realitySettings.put("shortId", params.getOrElse("sid") { "" })
        streamSettings.put("realitySettings", realitySettings)
    }

    val user = JSONObject()
    user.put("id", uuid)
    user.put("encryption", "none")
    if (flow.isNotBlank()) {
        user.put("flow", flow)
    }

    val vNext = JSONObject()
    vNext.put("address", host)
    vNext.put("port", port)
    vNext.put("users", JSONArray().put(user))

    val outboundSettings = JSONObject()
    outboundSettings.put("vnext", JSONArray().put(vNext))

    val inbound = JSONObject()
    inbound.put("port", 10808)
    inbound.put("listen", "127.0.0.1")
    inbound.put("protocol", "socks")
    val inboundSettings = JSONObject()
    inboundSettings.put("udp", true)
    inboundSettings.put("auth", "noauth")
    inbound.put("settings", inboundSettings)

    val outboundProxy = JSONObject()
    outboundProxy.put("protocol", "vless")
    outboundProxy.put("tag", "proxy")
    outboundProxy.put("settings", outboundSettings)
    outboundProxy.put("streamSettings", streamSettings)

    val outboundDirect = JSONObject()
    outboundDirect.put("protocol", "freedom")
    outboundDirect.put("tag", "direct")

    val serverEntry = JSONObject()
    serverEntry.put("address", host)
    serverEntry.put("port", port)
    serverEntry.put("protocol", "vless")
    serverEntry.put("settings", outboundSettings)
    serverEntry.put("streamSettings", streamSettings)

    val domainRule = JSONObject() // русский трафик идет как обычно, весь другой через тунель
    domainRule.put("type", "field")
    domainRule.put("domain", JSONArray().put("domain:.ru").put("domain:.su").put("domain:.рф"))
    domainRule.put("outboundTag", "direct")

    val ipRule = JSONObject()
    ipRule.put("type", "field")
    ipRule.put("ip", JSONArray().put("geoip:private").put("geoip:ru"))
    ipRule.put("outboundTag", "direct")

    val catchAllRule = JSONObject()
    catchAllRule.put("type", "field")
    catchAllRule.put("network", "tcp,udp")
    catchAllRule.put("outboundTag", "proxy")

    val routing = JSONObject()
    routing.put("domainStrategy", "IPIfNonMatch")
    routing.put("rules", JSONArray().put(domainRule).put(ipRule).put(catchAllRule))

    val config = JSONObject()
    config.put("log", JSONObject().apply { put("loglevel", "warning") })
    config.put("inbounds", JSONArray().put(inbound))
    config.put("outbounds", JSONArray().put(outboundProxy).put(outboundDirect))
    config.put("routing", routing)
    config.put("servers", JSONArray().put(serverEntry))

    return config.toString()
}
// парсер для работы ссылками VLESS
object MarzbanApi {
    suspend fun getConfigFromInput(input: String): String {
        val trimmedInput = input.trim()

        if (trimmedInput.startsWith("vless://", ignoreCase = true)) {
            return trimmedInput
        }

        if (trimmedInput.startsWith("http://", ignoreCase = true) || trimmedInput.startsWith("https://", ignoreCase = true)) {
            return withContext(Dispatchers.IO) {
                val url = URL(trimmedInput)
                val conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "GET"
                conn.connectTimeout = 5000
                conn.readTimeout = 5000

                if (conn.responseCode != 200) throw Exception("Ссылка подписки недействительна (код ${conn.responseCode})")

                val base64Data = conn.inputStream.bufferedReader().readText()
                val decodedBytes = Base64.decode(base64Data, Base64.DEFAULT)
                val decodedString = String(decodedBytes)

                decodedString.lines().firstOrNull { it.trim().startsWith("vless://", ignoreCase = true) }
                    ?: throw Exception("VLESS не найден в подписке")
            }
        }

        throw Exception("Неподдерживаемый формат. Вставьте vless:// или https://")
    }
}