# GPS Position

> iOS 虛擬定位工具 · 無需越獄 · 本機運行
> Supports iPhone (real device) via USB · macOS only

---

## 目錄

- [架構總覽](#架構總覽)
- [技術棧](#技術棧)
- [系統需求](#系統需求)
- [安裝](#安裝)
- [啟動方式](#啟動方式)
- [功能一覽](#功能一覽)
- [API 端點](#api-端點)
- [專案結構](#專案結構)
- [安全說明](#安全說明)

---

## 架構總覽

```
┌─────────────────────────────────────────────────────┐
│                    Browser UI                        │
│              http://localhost:7788/ui                │
│         (index.html · Leaflet · Vanilla JS)          │
└────────────────────┬────────────────────────────────┘
                     │  HTTP REST (localhost only)
┌────────────────────▼────────────────────────────────┐
│               Flask Backend                          │
│            gps_position.py :7788                     │
│                                                      │
│  ┌─────────────┐   ┌──────────────────────────────┐ │
│  │   Tunnel    │   │    LocationController        │ │
│  │  (subprocess│   │  (asyncio · 持久連線)         │ │
│  │  lockdown   │   │  set_location() 非阻塞呼叫    │ │
│  │  start-tunnel)  │  keep-alive 每 5s            │ │
│  └──────┬──────┘   └──────────────┬───────────────┘ │
└─────────┼────────────────────────┼─────────────────┘
          │ USB (RSD tunnel)        │ DVT Protocol
┌─────────▼────────────────────────▼─────────────────┐
│                    iPhone                            │
│         pymobiledevice3 · LocationSimulation         │
│              寫入假 GPS 座標至系統層                  │
└─────────────────────────────────────────────────────┘
```

### 核心流程

```
啟動後端 (sudo)
  → 建立 USB Tunnel (RSD lockdown)
  → 取得 RSD Address + Port
  → LocationController 建立持久 DVT 連線
  → 前端呼叫 /location/set → controller.set_location()
  → asyncio event loop 寫入 iPhone GPS
```

### LocationController 設計

- 單一背景執行緒跑 asyncio event loop，全程保持連線
- `set_location()` 從任意執行緒呼叫，`call_soon_threadsafe` 非阻塞傳送
- **Keep-alive**：閒置超過 5 秒自動重送最後座標，防止 DVT session 斷線
- **指數退避重連**：連線中斷自動重試，間隔 1s → 2s → 4s … 最長 16s

---

## 技術棧

### 後端

| 套件 | 版本 | 用途 |
|------|------|------|
| Python | 3.9+ | 執行環境 |
| [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) | 9.12+ | iPhone DVT 通訊協議 |
| Flask | 3.x | REST API Server |
| flask-cors | 6.x | Cross-Origin 支援 |
| asyncio | stdlib | 持久連線 event loop |
| threading | stdlib | 背景執行緒管理 |

### 前端

| 技術 | 用途 |
|------|------|
| Vanilla JavaScript (ES6+) | 所有互動邏輯 |
| [Leaflet.js](https://leafletjs.com/) 1.9.4 | 互動地圖 |
| OpenStreetMap | 地圖圖層 |
| [Nominatim](https://nominatim.org/) | 地名搜尋 |
| [OpenRouteService](https://openrouteservice.org/) | 步行 / 騎車路線規劃（需 API Key） |
| localStorage | 設定 / 儲存地點 / 冷卻狀態持久化 |
| CSS backdrop-filter | 毛玻璃浮動面板 |
| Google Fonts (Orbitron, Share Tech Mono) | 顯示字型 |

---

## 系統需求

| 項目 | 需求 |
|------|------|
| macOS | 13 Ventura 以上 |
| Python | 3.9 以上 |
| iPhone | iOS 16+，A12 晶片以上 |
| 連線方式 | USB-C 或 Lightning |
| 瀏覽器 | Chrome / Safari / Firefox（需 localhost） |
| 權限 | `sudo`（建立 USB Tunnel 需要） |

---

## 安裝

```bash
# 複製專案
git clone <repo-url>
cd gps_position

# 安裝 Python 套件（只需執行一次）
pip3 install pymobiledevice3 flask flask-cors --user
```

### iPhone 前置設定（只需做一次）

1. **開啟開發者模式**
   ```
   設定 → 隱私權與安全性 → 開發者模式 → 開啟 → 重新開機
   ```

2. **信任電腦**
   USB 連接後，iPhone 點「**信任**」並輸入密碼

3. **掛載 DDI**（開發者映像檔）
   啟動後端後，在 UI 點「**⚙ 掛載 DDI**」，等待約 30 秒
   > iOS 系統更新後需重新執行此步驟

---

## 啟動方式

### 啟動後端

```bash
cd ~/gps_position
sudo python3 gps_position.py
```

成功輸出：

```
🛰  GPS Spoofer v2 — http://localhost:7788/ui
   持久連線模式：無 subprocess pool，無飄移風險
```

### 開啟前端 UI

```
http://localhost:7788/ui
```

> ⚠️ 必須透過 `http://localhost:7788/ui` 開啟
> 直接用 `open index.html`（file://）**無法**取得瀏覽器定位權限

### 連線步驟

```
1. 確認 iPhone 螢幕解鎖、USB 連接
2. 瀏覽器開啟 http://localhost:7788/ui
3. 點「▶ 啟動通道」→ 等待「通道已連線」
4. 首次使用點「⚙ 掛載 DDI」（約 30 秒）
5. 點地圖或輸入座標 → 點「▶ 設定此位置」
```

---

## 功能一覽

| 功能 | 說明 |
|------|------|
| **傳送（Teleport）** | 點地圖或輸入座標，瞬間跳至指定位置 |
| **冷卻計時** | 依位移距離自動計算冷卻時間，防止異常偵測 |
| **路線移動（Route）** | 沿設定路線連續移動，支援暫停 / 即時變速 |
| **步行 / 騎車路線** | 串接 OpenRouteService，沿真實道路規劃路線 |
| **GPX 匯入** | 載入 `.gpx` 檔案播放路線 |
| **批次座標匯入** | 貼上多行座標一次匯入為路線點 |
| **隨機漫步（Walk）** | 在指定半徑內自然閒逛，方向帶慣性 |
| **鍵盤控制** | WASD / 方向鍵移動，可調步長，浮動面板（啟用時外框發光）|
| **計時器** | 多計時器同步執行，可標記已儲存地點；右側固定側抽屜 |
| **書籤地點** | 常用座標書籤管理；自動在地圖顯示 pin + 名稱，點擊可傳送 |
| **系統日誌** | 底部常駐 drawer，點擊 header 展開 / 收合 |
| **設定面板** | 字體縮放、API port、地圖預設視角、冷卻參數集中管理 |

---

## API 端點

所有端點綁定 `127.0.0.1:7788`，僅限本機存取。

| Method | Path | 說明 |
|--------|------|------|
| `GET` | `/ui` | 回傳前端 index.html |
| `GET` | `/status` | 通道狀態 + controller 連線狀態 |
| `POST` | `/tunnel/start` | 建立 USB RSD Tunnel |
| `POST` | `/tunnel/stop` | 關閉 Tunnel |
| `POST` | `/tunnel/set-rsd` | 手動設定 RSD 位址 |
| `GET` | `/tunnel/logs` | 最近 50 行 Tunnel 日誌 |
| `POST` | `/location/set` | 設定虛假 GPS 座標 `{lat, lng}` |
| `POST` | `/location/clear` | 還原真實 GPS |
| `POST` | `/route/play` | 開始路線播放 `{points, speed, loops}` |
| `POST` | `/route/pause` | 暫停路線 |
| `POST` | `/route/resume` | 繼續路線 |
| `POST` | `/route/stop` | 停止路線 |
| `POST` | `/route/speed` | 即時變更路線速度 |
| `GET` | `/route/status` | 路線播放進度 |
| `POST` | `/route/random-walk` | 開始隨機漫步 |
| `POST` | `/mounter/mount` | 掛載 DDI 開發者映像檔 |

---

## 專案結構

```
gps_position/
├── gps_position.py     # Flask 後端 + LocationController
├── index.html          # 前端單頁應用
├── README.md           # 本文件
├── 使用說明.md         # 使用者操作說明
└── 技術文件.md         # 技術架構細節
```

### index.html 內部結構（單檔）

```
index.html
├── <style>             # 所有 CSS（~820 行）
├── HTML                # 骨架結構：
│   ├── #top-bar        → fixed 頂部列（連線控制、書籤、計時器、設定）
│   ├── #timer-panel    → fixed 右側計時器側抽屜
│   ├── #saved-modal    → 書籤管理 modal
│   ├── #settings-modal → 系統設定 modal
│   └── #app
│       ├── #panel      → 左側可收合功能面板（傳送/路線/GPX/漫步）
│       ├── #panel-toggle → ‹/› 收合按鈕
│       └── #map-wrap
│           ├── #map            → Leaflet 地圖
│           ├── #mc             → 地圖浮動控件（含 📌 書籤 pin toggle）
│           ├── #hud            → 座標 / 狀態 / 速度 / 偽裝 badge
│           ├── #kb-float       → 鍵盤控制浮動面板（右下）
│           ├── #log-float      → 系統日誌底部 drawer（底部中央）
│           └── #rp             → 路線進度條
└── <script>            # 所有 JS（~3,200 行），依功能分區塊：
    ├── panel           → 面板收合 togglePanel()
    ├── config          → 常數 + DEFAULT_SETTINGS
    ├── state           → 全域狀態
    ├── map             → Leaflet 地圖操作
    ├── tunnel          → USB Tunnel 管理
    ├── teleport        → 座標傳送 + 搜尋
    ├── saved           → 書籤地點 + 地圖 pins（renderSavedMarkers）
    ├── cooldown        → 冷卻計算
    ├── interpolation   → 路徑插值（純函式）
    ├── route           → 路線播放 + HUD 速度顯示
    ├── gpx             → GPX 解析
    ├── ors             → OpenRouteService
    ├── batch           → 批次匯入
    ├── random-walk     → 隨機漫步
    ├── keyboard        → 鍵盤控制
    ├── timer           → 計時器
    ├── settings        → 設定管理
    └── init            → 初始化 + 狀態恢復
```

---

## 安全說明

所有通訊在本機進行，不連接外部伺服器（以下除外）。

| 元件 | 連線對象 | 資料內容 |
|------|---------|---------|
| 後端 API | `127.0.0.1:7788`（本機） | 座標、控制指令 |
| USB Tunnel | iPhone（本機 USB） | DVT 協議，僅寫 GPS |
| 地圖圖層 | OpenStreetMap CDN | 地圖磚（IP 可見） |
| 地名搜尋 | Nominatim | 搜尋關鍵字 |
| 路線規劃 | OpenRouteService | 路線座標（需 API Key） |
| 儲存資料 | `localStorage`（瀏覽器本機） | 書籤、冷卻狀態、設定 |

**iPhone 私人資料**：後端僅透過 Apple DVT 協議寫入虛假 GPS 座標，不讀取任何裝置資料（無 UDID、無帳號、無個人資訊）。

---

---

## UI 佈局概覽

```
┌─────────────────────────────────────────────────────────────┐
│ #top-bar  ● GPS 定位 · 狀態 · ▶啟動 ■停止 ⚙DDI · 📂書籤 ⏱計時器 · 設定 │
├──────────┬──────────────────────────────────────┬──────────┤
│          │                                      │ #timer   │
│ #panel   │        Leaflet Map                   │  panel   │
│ (可收合) │  ┌─ #mc ────────────────┐            │ (fixed   │
│ ‹ / ›   │  │ ◎ ⊕ 📌 ✚ ⌨ LOG      │            │ 右側     │
│ [傳送]   │  └──────────────────────┘            │ 側抽屜)  │
│ [路線]   │                                      │          │
│ [GPX]    │  #hud（座標·狀態·速度·偽裝中）         │          │
│ [漫步]   │                                      │          │
├──────────┴──────────────────────────────────────┴──────────┤
│         #log-float  系統日誌 drawer（底部中央，點擊展開）         │
└─────────────────────────────────────────────────────────────┘
```

*GPS Position v2 · iOS 16+ · macOS 13+ · pymobiledevice3*
