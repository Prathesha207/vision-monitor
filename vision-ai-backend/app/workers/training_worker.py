from app.ai.trainer import generate_training_script

#ruunning training
def run_training(dataset_path):

    script = generate_training_script(dataset_path)

    print("Running training...")

    print(script)

    return {"status": "training started"}