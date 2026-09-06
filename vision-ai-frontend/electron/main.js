const {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  protocol,
  net,
} = require("electron")

const path = require("path")
const axios = require("axios")
const { spawn, execSync } = require("child_process")
const fs = require("fs")
const netSocket = require("net")

/* =========================================================
   1. SINGLE INSTANCE LOCK (MUST BE BEFORE ANY PROCESS OPERATIONS)
========================================================= */
const gotTheLock = app.requestSingleInstanceLock()
if (!gotTheLock) {
  console.log("Another instance of Vision AI is already running. Focusing existing window and quitting duplicate instance.")
  app.quit()
  process.exit(0)
}

let mainWindow = null
let splashWindow = null
let backendProcess = null
let isQuitting = false

app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.focus()
  }
})

app.disableHardwareAcceleration()

process.on("uncaughtException", (err) => {
  console.error("UNCAUGHT EXCEPTION:", err)
})

process.on("unhandledRejection", (err) => {
  console.error("UNHANDLED REJECTION:", err)
})

const API_BASE = "http://127.0.0.1:8000"
const isDev = !app.isPackaged

/* =========================================================
   AUTHORITATIVE USER DATA DIRECTORY & PID MARKER
========================================================= */
function getUserDataDir() {
  if (process.platform === "win32") {
    const root = process.env.LOCALAPPDATA || process.env.APPDATA || path.join(process.env.USERPROFILE || "", "AppData", "Local")
    return path.join(root, "Vision-AI")
  }
  const xdg = process.env.XDG_STATE_HOME || path.join(process.env.HOME || "", ".local", "state")
  return path.join(xdg, "vision-ai")
}

const DATA_DIR = getUserDataDir()
const PID_FILE = path.join(DATA_DIR, "backend.pid")

function saveBackendPid(pid) {
  try {
    fs.mkdirSync(DATA_DIR, { recursive: true })
    fs.writeFileSync(PID_FILE, String(pid), "utf8")
  } catch (err) {
    console.error("Could not write backend.pid:", err.message)
  }
}

function clearBackendPid() {
  try {
    if (fs.existsSync(PID_FILE)) {
      fs.unlinkSync(PID_FILE)
    }
  } catch (err) {
    console.error("Could not remove backend.pid:", err.message)
  }
}

function killProcessTreeSync(pid) {
  if (!pid) return
  try {
    if (process.platform === "win32") {
      execSync(`taskkill /F /T /PID ${pid}`, { stdio: "ignore" })
    } else {
      process.kill(-pid, "SIGKILL")
    }
  } catch (e) {
    try {
      process.kill(pid, "SIGKILL")
    } catch (e2) {}
  }
}

async function cleanupStaleBackend() {
  if (!fs.existsSync(PID_FILE)) return

  try {
    const oldPidStr = fs.readFileSync(PID_FILE, "utf8").trim()
    const oldPid = parseInt(oldPidStr, 10)
    if (!oldPid || isNaN(oldPid)) {
      clearBackendPid()
      return
    }

    let isAlive = false
    try {
      process.kill(oldPid, 0)
      isAlive = true
    } catch (e) {
      isAlive = false
    }

    if (isAlive) {
      console.log(`Found previous backend process with PID ${oldPid}. Terminating process tree...`)
      killProcessTreeSync(oldPid)
      await new Promise((resolve) => setTimeout(resolve, 500))
    }
  } catch (err) {
    console.error("Error inspecting stale backend PID:", err.message)
  } finally {
    clearBackendPid()
  }
}

function checkPortAvailable(port) {
  return new Promise((resolve) => {
    const s = netSocket.createServer()
    s.once("error", () => resolve(false))
    s.once("listening", () => {
      s.close()
      resolve(true)
    })
    s.listen(port, "127.0.0.1")
  })
}

/* =========================================================
   APP PROTOCOL
========================================================= */
protocol.registerSchemesAsPrivileged([
  {
    scheme: "app",
    privileges: {
      secure: true,
      standard: true,
      corsEnabled: true,
      supportFetchAPI: true,
    },
  },
])

function getStaticPath() {
  return path.join(__dirname, "..", "dist")
}

/* =========================================================
   SPLASH WINDOW
========================================================= */
function createSplashWindow() {
  const iconPath = process.platform === "win32"
    ? path.join(__dirname, "..", "public", "icon.ico")
    : path.join(__dirname, "..", "public", "icon.png")

  splashWindow = new BrowserWindow({
    width: 420,
    height: 320,
    frame: false,
    transparent: false,
    alwaysOnTop: true,
    center: true,
    resizable: false,
    movable: false,
    fullscreenable: false,
    backgroundColor: "#0B1020",
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
  })

  splashWindow.loadURL(`
    data:text/html;charset=UTF-8,
    <html>
      <body style="
        margin:0;
        display:flex;
        justify-content:center;
        align-items:center;
        flex-direction:column;
        background:#0B1020;
        color:white;
        font-family:sans-serif;
        height:100vh;
      ">
        <h1 style="margin-bottom:10px;">
          Vision AI
        </h1>

        <p style="opacity:0.7">
          Starting backend services...
        </p>

        <div style="
          margin-top:20px;
          width:220px;
          height:6px;
          background:#1E293B;
          border-radius:999px;
          overflow:hidden;
        ">
          <div style="
            width:40%;
            height:100%;
            background:#06B6D4;
            animation: loading 1s infinite;
          "></div>
        </div>

        <style>
          @keyframes loading {
            0% { transform: translateX(-100%); }
            100% { transform: translateX(350%); }
          }
        </style>
      </body>
    </html>
  `)
}

/* =========================================================
   MAIN WINDOW
========================================================= */
function createWindow() {
  const iconPath = process.platform === "win32"
    ? path.join(__dirname, "..", "public", "icon.ico")
    : path.join(__dirname, "..", "public", "icon.png")

  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    show: false,
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: false,
      allowRunningInsecureContent: true,
    },
  })

  mainWindow.webContents.on("did-fail-load", (event, code, desc) => {
    console.error("FAILED TO LOAD:", code, desc)
  })

  mainWindow.webContents.on("render-process-gone", (event, details) => {
    console.error("RENDER PROCESS GONE:", details)
  })

  mainWindow.on("closed", () => {
    console.log("Main window closed")
  })

  if (!app.isPackaged) {
    mainWindow.loadURL("http://localhost:5173")
    mainWindow.webContents.openDevTools()
  } else {
    const indexPath = path.join(__dirname, "..", "dist", "index.html")
    console.log("Loading packaged index.html from:", indexPath)
    mainWindow.loadFile(indexPath)
  }

  mainWindow.once("ready-to-show", () => {
    if (splashWindow) {
      splashWindow.destroy()
      splashWindow = null
    }

    mainWindow.show()
  })

  return mainWindow
}

/* =========================================================
   START BACKEND
========================================================= */
async function startBackend() {
  if (!app.isPackaged) {
    console.log("Development mode - backend handled separately")
    return
  }

  // 1. Clean up stale backend if previously recorded in PID marker
  await cleanupStaleBackend()

  // 2. Pre-flight check on port 8000
  const portFree = await checkPortAvailable(8000)
  if (!portFree) {
    try {
      const res = await axios.get(`${API_BASE}/health`, { timeout: 1500 })
      if (res.data && res.data.status === "ready") {
        console.log("Existing backend is already healthy and responsive. Reusing instance.")
        return
      }
    } catch (e) {}

    console.warn("Port 8000 is occupied. Proceeding with backend spawn attempt...")
  }

  const backendExecutable = process.platform === "win32" ? "backend.exe" : "backend"
  const backendPath = path.join(process.resourcesPath, "backend", backendExecutable)

  console.log("Starting backend:", backendPath)

  if (!fs.existsSync(backendPath)) {
    console.error("Backend executable not found:", backendPath)
    return
  }

  if (process.platform !== "win32") {
    try { fs.chmodSync(backendPath, 0o755) } catch (e) {}
  }

  backendProcess = spawn(backendPath, [], {
    shell: false,
    detached: process.platform !== "win32",
    windowsHide: true,
  })

  if (backendProcess && backendProcess.pid) {
    saveBackendPid(backendProcess.pid)
  }

  backendProcess.on("error", (error) => {
    console.error("Could not launch bundled backend:", error)
    clearBackendPid()
  })

  backendProcess.on("exit", (code, signal) => {
    console.log("Bundled backend exited:", { code, signal })
    backendProcess = null
    clearBackendPid()
  })
}

/* =========================================================
   WAIT FOR BACKEND
========================================================= */
async function waitForBackend() {
  let backendReady = false
  const startTime = Date.now()

  while (!backendReady) {
    try {
      const res = await axios.get(`${API_BASE}/health`, {
        timeout: 1000,
      })

      if (res.status === 200) {
        backendReady = true
        console.log("Backend ready confirmed")
        break
      }
    } catch (err) {
      if (Date.now() - startTime > 25000) {
        console.warn("Backend startup wait limit reached; proceeding.")
        break
      }
      await new Promise((resolve) => setTimeout(resolve, 250))
    }
  }
}

/* =========================================================
   STOP BACKEND
========================================================= */
async function stopBackend() {
  const pidToStop = backendProcess ? backendProcess.pid : null

  // 1. Tell backend to stop active cameras/inference
  try {
    await axios.post(`${API_BASE}/oak/oak/stop`, {}, { timeout: 800 })
  } catch (err) {}

  // 2. Request graceful backend shutdown
  try {
    await axios.post(`${API_BASE}/api/system/shutdown`, {}, { timeout: 800 })
  } catch (err) {}

  // 3. Grace wait for process to finish exiting
  await new Promise((resolve) => setTimeout(resolve, 500))

  // 4. Force process tree cleanup if still alive
  if (pidToStop) {
    try {
      process.kill(pidToStop, 0)
      console.log(`Backend PID ${pidToStop} still running; terminating process tree...`)
      killProcessTreeSync(pidToStop)
    } catch (e) {
      // Already exited cleanly
    }
  }

  backendProcess = null
  clearBackendPid()
}

/* =========================================================
   APP READY
========================================================= */
app.whenReady().then(async () => {
  if (!isDev) {
    const staticPath = getStaticPath()

    protocol.handle("app", (request) => {
      const url = new URL(request.url)
      let pathname = url.pathname

      if (pathname === "/" || pathname === "") {
        pathname = "/index.html"
      } else if (!path.extname(pathname)) {
        pathname = pathname.replace(/\/?$/, "/index.html")
      }

      const filePath = path.join(staticPath, pathname)
      return net.fetch(`file://${filePath}`).catch(() =>
        net.fetch(`file://${path.join(staticPath, "index.html")}`)
      )
    })
  }

  console.log("Electron app ready")
  console.log("NODE_ENV:", process.env.NODE_ENV)
  console.log("app.isPackaged:", app.isPackaged)

  createSplashWindow()
  console.log("Splash window created")

  await startBackend()
  console.log("Backend start triggered")

  await waitForBackend()
  console.log("Backend ready confirmed")

  createWindow()
  console.log("Main window created")
})

/* =========================================================
   IPC HANDLERS
========================================================= */
const logFilePath = path.join(app.getPath("userData"), "log.txt")

ipcMain.handle("write-log", (_event, message) => {
  const timestamp = new Date().toISOString()
  const line = `[${timestamp}] ${message}\n`
  fs.appendFileSync(logFilePath, line, "utf8")
})

ipcMain.handle("select-folder", async () => {
  const result = await dialog.showOpenDialog({
    properties: ["openDirectory"],
  })

  if (result.canceled) return null
  return result.filePaths[0]
})

ipcMain.handle("select-file", async () => {
  const result = await dialog.showOpenDialog({
    properties: ["openFile"],
    filters: [
      {
        name: "Videos",
        extensions: ["mp4", "avi", "mov", "mkv", "wmv", "flv", "m4v", "webm"],
      },
    ],
  })

  if (result.canceled) return null
  return result.filePaths[0]
})

/* =========================================================
   SAFE EXIT & LIFECYCLE
========================================================= */
app.on("before-quit", async (event) => {
  if (isQuitting) return
  event.preventDefault()
  isQuitting = true

  console.log("Stopping backend before quit...")
  await stopBackend()
  app.quit()
})

app.on("window-all-closed", async () => {
  await stopBackend()
  if (process.platform !== "darwin") {
    app.quit()
  }
})

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})
