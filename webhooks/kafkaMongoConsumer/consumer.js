const { Kafka } = require("kafkajs");
const { MongoClient } = require("mongodb");

const kafkaBrokers = process.env.KAFKA_BROKERS.split(",");
const kafkaTopic = process.env.KAFKA_TOPIC;
const kafkaGroupId = process.env.KAFKA_GROUP_ID || "mongo-writer-group";

const mongoUri = process.env.MONGO_URI;
const mongoDb = process.env.MONGO_DB;
const mongoCollection = process.env.MONGO_COLLECTION;

const kafka = new Kafka({
  clientId: "kafka-mongo-consumer",
  brokers: kafkaBrokers,
});

const consumer = kafka.consumer({
  groupId: kafkaGroupId,
});

const mongoClient = new MongoClient(mongoUri);

async function main() {
  await mongoClient.connect();
  console.log("Connected to MongoDB");

  const db = mongoClient.db(mongoDb);
  const collection = db.collection(mongoCollection);

  await consumer.connect();
  console.log("Connected to Kafka");

  await consumer.subscribe({
    topic: kafkaTopic,
    fromBeginning: false,
  });

  await consumer.run({
    eachMessage: async ({ topic, partition, message }) => {
      try {
        const raw = message.value.toString();
        const doc = JSON.parse(raw);

        await collection.insertOne({
          ...doc,
          _kafka: {
            topic,
            partition,
            offset: message.offset,
            timestamp: message.timestamp,
          },
          ingested_at: new Date(),
        });

        console.log(`Inserted Kafka offset ${message.offset}`);
      } catch (err) {
        console.error("Failed to insert message:", err.message);
      }
    },
  });
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});