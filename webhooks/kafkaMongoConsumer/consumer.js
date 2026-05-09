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
  console.log("Starting Kafka to Mongo consumer");
  console.log(`Kafka brokers: ${kafkaBrokers.join(",")}`);
  console.log(`Kafka topic: ${kafkaTopic}`);
  console.log(`Kafka groupId: ${kafkaGroupId}`);
  console.log(`Mongo URI: ${mongoUri}`);
  console.log(`Mongo DB: ${mongoDb}`);
  console.log(`Mongo collection: ${mongoCollection}`);

  await mongoClient.connect();
  console.log("Connected to MongoDB");

  const db = mongoClient.db(mongoDb);
  const collection = db.collection(mongoCollection);

  await consumer.connect();
  console.log("Connected to Kafka");

  await consumer.subscribe({
    topic: kafkaTopic,
    fromBeginning: true,
  });

  await consumer.run({
    eachMessage: async ({ topic, partition, message }) => {
      try {
        const raw = message.value.toString();

        console.log(
          `Received message topic=${topic} partition=${partition} offset=${message.offset}`
        );

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

        console.log(
          `Inserted Kafka message topic=${topic} partition=${partition} offset=${message.offset}`
        );
      } catch (err) {
        console.error("Failed to process message");
        console.error(err);
      }
    },
  });
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});