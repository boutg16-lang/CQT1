# OUSSAMA Cutter Android release rules.
# kotlinx.serialization keeps generated serializers referenced by reflection.
-keepclassmembers class **$$serializer { *; }
-keepclassmembers class **$Companion { *; }
-keep @kotlinx.serialization.Serializable class * { *; }
-dontwarn kotlinx.serialization.**
