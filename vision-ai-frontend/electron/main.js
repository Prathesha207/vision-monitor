

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
const { spawn } = require("child_process")
const fs = require("fs")
app.disableHardwareAcceleration()

process.on("uncaughtException", (err) => {
  console.error("UNCAUGHT EXCEPTION:", err)
})

process.on("unhandledRejection", (err) => {
  console.error("UNHANDLED REJECTION:", err)
})

const API_BASE = "http://127.0.0.1:8000"

//const isDev = process.env.NODE_ENV === "development"
const isDev = !app.isPackaged
let backendProcess = null
let mainWindow = null
let splashWindow = null
let isQuitting = false

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

/* =========================================================
   STATIC PATH
========================================================= */

function getStaticPath() {
  return path.join(__dirname, "..", "dist")
}

/* =========================================================
   SPLASH WINDOW
========================================================= */

function createSplashWindow() {
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
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    show: false,

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

function startBackend() {
  if (!app.isPackaged) {
    console.log("Development mode - backend handled separately")
    return
  }

  const backendExecutable =
    process.platform === "win32"
      ? "backend.exe"
      : "backend"

  const backendPath = path.join(
    process.resourcesPath,
    "backend",
    backendExecutable
  )

  console.log("Starting backend:", backendPath)

  if (!fs.existsSync(backendPath)) {
    console.error("Backend executable not found:", backendPath)
    return
  }

  if (process.platform !== "win32") {
    fs.chmodSync(backendPath, 0o755)
  }

  backendProcess = spawn(backendPath, [], {
    shell: false,
    detached: false,
    windowsHide: true,
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
      await axios.get(`${API_BASE}/health`, {
        timeout: 1000,
      })

      backendReady = true
      console.log("Backend ready")
    } catch (err) {
      console.log("Waiting for backend...")
      if (Date.now() - startTime > 15000) {
        console.warn("Backend startup wait limit reached; proceeding.")
        break
      }
      await new Promise((resolve) =>
        setTimeout(resolve, 250)
      )
    }
  }
}

/* =========================================================
   STOP BACKEND
========================================================= */

async function stopBackend() {
  try {
    await axios.post(`${API_BASE}/oak/oak/stop`)
  } catch (err) {
    console.error(
      "Failed to stop backend safely:",
      err.message
    )
  }

  if (backendProcess) {
    try {
      backendProcess.kill("SIGTERM")
    } catch (err) {
      console.error("Kill failed:", err.message)
    }

    backendProcess = null
  }
}

/* =========================================================
   APP READY
========================================================= */

app.whenReady().then(async () => {
  /* ======================
     APP PROTOCOL
  ====================== */

  if (!isDev) {
    const staticPath = getStaticPath()

    protocol.handle("app", (request) => {
      const url = new URL(request.url)

      let pathname = url.pathname

      if (pathname === "/" || pathname === "") {
        pathname = "/index.html"
      } else if (!path.extname(pathname)) {
        pathname = pathname.replace(
          /\/?$/,
          "/index.html"
        )
      }

      const filePath = path.join(
        staticPath,
        pathname
      )

      return net.fetch(`file://${filePath}`).catch(() =>
        net.fetch(
          `file://${path.join(
            staticPath,
            "index.html"
          )}`
        )
      )
    })
  }

console.log("Electron app ready")
console.log("NODE_ENV:", process.env.NODE_ENV)
console.log("app.isPackaged:", app.isPackaged)

createSplashWindow()
console.log("Splash window created")

startBackend()
console.log("Backend start triggered")

await waitForBackend()
console.log("Backend ready confirmed")

createWindow()
console.log("Main window created")
})

/* =========================================================
   IPC
========================================================= */

/* =========================================================
   LOGGING
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
   SAFE EXIT
========================================================= */

app.on("before-quit", async (event) => {
  if (isQuitting) return

  event.preventDefault()

  isQuitting = true

  console.log("Stopping backend...")

  await stopBackend()

  app.quit()
})

app.on("window-all-closed", async () => {
  await stopBackend()

  if (process.platform !== "darwin") {
    app.quit()
  }
})

/* =========================================================
   MACOS ACTIVATE
========================================================= */

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})