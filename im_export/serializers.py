from rest_framework import serializers

class BankImportUploadSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)