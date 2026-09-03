import argparse

from app.core.database import Base, SessionLocal, engine
from app.models.camera_model import Camera


def discover_devices():
    """Return devices discovered by DepthAI on this computer."""
    try:
        import depthai as dai
    except Exception as error:
        print(f"DepthAI is not available: {error}")
        return []

    try:
        return dai.Device.getAllAvailableDevices()
    except Exception as error:
        print(f"Could not scan for OAK cameras: {error}")
        return []


def check_camera_connections(devices):
    """Open each discovered device briefly to verify an actual connection."""
    import depthai as dai

    connected = []
    for index, device_info in enumerate(devices, start=1):
        device_id = str(getattr(device_info, "deviceId", "") or getattr(device_info, "mxid", "") or device_info)
        try:
            device = dai.Device(device_info)
            actual_info = device.getDeviceInfo()
            actual_id = str(getattr(actual_info, "deviceId", "") or getattr(actual_info, "mxid", "") or device_id)
            connected.append((actual_id, device_info))
            print(f"  - Camera {index}: CONNECTED | MXID: {actual_id}")
            device.close()
        except Exception as error:
            print(f"  - Device {index}: FOUND but connection failed | {device_id} | {error}")

    return connected


def seed_connected_cameras(db, connected_devices):
    """Create database rows only for devices that opened successfully."""
    existing = {camera.ip_address: camera for camera in db.query(Camera).all()}
    for index, (device_id, _) in enumerate(connected_devices, start=1):
        if device_id in existing:
            continue

        camera = Camera(
            name=f"OAK Camera {index}",
            ip_address=device_id,
            resolution="1920x1080",
            fps=30,
            is_enabled=True,
        )
        db.add(camera)

    db.commit()
    return db.query(Camera).all()

def main():
    parser = argparse.ArgumentParser(description="Check OAK cameras and optionally seed connected devices.")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Insert successfully connected OAK devices into the cameras table.",
    )
    args = parser.parse_args()

    print("==================================================")
    print("       OAK Camera Connection Check               ")
    print("==================================================")

    print("\nScanning Linux USB/network devices with DepthAI...")
    devices = discover_devices()
    print(f"Found {len(devices)} DepthAI device entries.")

    if not devices:
        print("No OAK device was discovered. Check USB, power, permissions, and the DepthAI installation.")
        return

    connected_devices = check_camera_connections(devices)
    print(f"\nActually connected: {len(connected_devices)} of {len(devices)}")

    if args.seed and connected_devices:
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            seed_connected_cameras(db, connected_devices)
            print("Connected cameras were added to the database.")
        finally:
            db.close()
    elif not args.seed:
        print("Database was not changed. Use --seed only when you want to add connected cameras.")

if __name__ == "__main__":
    main()
